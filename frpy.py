"""
frpy
A simple reverse proxy to help you expose a local server behind a NAT or firewall to the internet. (a imitator of frp)

[Architecture]
frpy_server —— worker_server —— user_side
|
frpy_client —— local_server_side

[Usage]
-- server side --
# start frpy.py server on 8000 port
$ python frpy.py server 0.0.0.0 8000

-- client side --
# start a website on 8080 port (or existing tcp service)
$ python -m http.server 8080

# start frpy.py client connecting to server, and expose the local tcp service to server
$ python frpy.py client server.domain.com 8000 127.0.0.1 8080 60080

# view the website on server with 60080 port
$ curl http://server.domain.com:60080

todo:
- improve concurrent performance and speed
- client can restart at will
- handle user disconnected
- test mysql
- test tslow.cn website
- ctrl + c to break server
"""

import logging
import random
import select
import socket
import sys
import threading
import time
import traceback
from _thread import start_new_thread


# =========== Conf ===========
SERVER_HOST = '127.0.0.1'
SERVER_PORT = 8000
LOCAL_HOST = '127.0.0.1'
LOCAL_PORT = 8080
REMOTE_PORT = 60080
BUFFER_SIZE = 1024
# =============================


logging.basicConfig(level=logging.DEBUG)
server_logger = logging.getLogger('Server')
client_logger = logging.getLogger('Client')
state = {}


# ---------- for server side ---------

class EasyTcpServer:
    """
    [server example]
    server = EasyTcpServer('127.0.0.1', 8000)
    server.run()

    [client example]
    import socket
    import time
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(('127.0.0.1', 8000))
    s.sendall(b'123')
    data = s.recv(1024)
    print(data)
    time.sleep(3)
    s.close()
    """

    def __init__(self, host='0.0.0.0', port=8000, buffer_size=1024):
        self.host = host
        self.port = port
        self.buffer_size = buffer_size
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)  # 设置地址可以重用
        self.server_socket.bind((host, port))
        self.server_socket.listen(10)
        self.client_sockets = []
        self.stopped = False

    def run(self):
        server_logger.info('[start tcp server] on %s:%d', self.host, self.port)
        try:
            while not self.stopped:
                client_socket, client_address = self.server_socket.accept()
                start_new_thread(self.handle_client, (client_socket, client_address), {})
                self.client_sockets.append(client_socket)
        except OSError as e:
            server_logger.debug(e)
        server_logger.info('[finish tcp server] on %s:%d', self.host, self.port)

    def shutdown(self):
        self.stopped = True
        for s in self.client_sockets:
            s.close()
        self.server_socket.close()
        server_logger.info('[shutdown tcp server] on %s:%d', self.host, self.port)

    def handle_client(self, client_socket, client_address):
        # handle a new client connection
        server_logger.info('[new client] on %s:%d, client address: %s', self.host, self.port, client_address)
        while True:
            # synchronous blocking
            data = client_socket.recv(self.buffer_size)
            self.handle_recv(client_socket, data)
            if data == b'':
                break
        self.client_sockets.remove(client_socket)
        client_socket.close()
        server_logger.info('[close client] on %s:%d, client address: %s', self.host, self.port, client_address)

    def handle_recv(self, client_socket, data):
        server_logger.warning('handle_recv should be override!')
        server_logger.debug(data)
        client_socket.sendall(data[::-1])
        if data == b'shutdown server':
            self.shutdown()


class WorkerServer(EasyTcpServer):

    def __init__(self, host='0.0.0.0', port=8000, buffer_size=1024, parent_socket=None, main_server=None):
        assert parent_socket, 'parent]_socket must been set'
        assert main_server, 'main_server must been set'
        self.parent_socket = parent_socket
        self.main_server = main_server
        super().__init__(host, port, buffer_size)

    def handle_recv(self, client_socket, data):
        server_logger.debug('data from user: %s', data)
        user_socket = client_socket

        # generate a random id (bytes, length: 8)
        # don't worry it will repeat
        n = random.randint(10000000, 99999999)
        user_id = str(n).encode()

        data = b'<frpy>' + user_id + b'|' + data + b'</frpy>'
        self.parent_socket.sendall(data)
        self.main_server.state[user_id] = {
            'parent_socket': self.parent_socket,
            'user_socket': user_socket,
        }


class MainServer(EasyTcpServer):

    workers = []
    state = {}

    def make_new_worker(self, port, client_socket):
        # make a new server in a new thread. [async]
        kwargs = {
            'remote_port': port,
            'parent_socket': client_socket,
        }
        start_new_thread(self.start_worker_server, (), kwargs)
        server_logger.debug(threading.enumerate())

    def start_worker_server(self, remote_port, parent_socket):
        # parent_socket is a socket between frpy server and frpy client
        worker_server = WorkerServer(port=remote_port, buffer_size=self.buffer_size, parent_socket=parent_socket, main_server=self)
        self.workers.append(worker_server)
        worker_server.run()

    def handle_client(self, client_socket, client_address):
        # handle a new frpy client connection
        server_logger.info('[new client] on %s:%d, client address: %s', self.host, self.port, client_address)

        buffer = b''
        while True:

            data = client_socket.recv(self.buffer_size + 22)
            server_logger.debug('data from client: %s', data)
            if data == b'':
                break

            buffer += data
            index = buffer.find(b'</frpy>')
            if index == -1:
                continue

            assert buffer.startswith(b'<frpy>'), buffer

            data = buffer[6:index]
            buffer = buffer[index + 7:]

            # synchronous blocking
            self.handle_recv(client_socket, data)

        self.client_sockets.remove(client_socket)
        client_socket.close()
        server_logger.info('[close client] on %s:%d, client address: %s', self.host, self.port, client_address)

    def split_data(self, data):
        assert b'|' in data, 'recv error data: %s' % data
        user_id, data = data.split(b'|', 1)
        assert len(user_id) == 8, 'recv error data: %s' % data
        return user_id, data

    def handle_recv(self, client_socket, data):
        # recv message from frpy client

        if not data:
            server_logger.warning('recv empty data')

        try:
            user_id, data = self.split_data(data)

            # if the first message, make a worker server
            if user_id == b'00000000':
                port = int(data.decode())
                self.make_new_worker(port, client_socket)
                return

            # other message will send to user
            user_socket = self.state.get(user_id, {}).get('user_socket')
            if not user_socket:
                server_logger.warning('#%s %s not found!', user_id, client_socket)
                server_logger.warning('state: %s', self.state)
                return

            user_socket.sendall(data)

        except Exception as e:
            server_logger.error('----- handle recv from client error -----')
            server_logger.error(e)
            server_logger.debug(traceback.format_exc())
            server_logger.error('-----------------------------------------')


# ---------- for client side ---------

def write_to_local(client_socket):
    buffer = b''
    while True:
        s_read, _, _ = select.select([client_socket], [], [], 0.5)
        if not s_read:
            continue

        s = s_read[0]
        data = s.recv(BUFFER_SIZE + 22)

        client_logger.debug('data from server %s', data)

        buffer += data
        index = buffer.find(b'</frpy>')
        if index == -1:
            continue

        assert buffer.startswith(b'<frpy>'), buffer

        data = buffer[6:index]
        buffer = buffer[index+7:]

        user_id, data = data.split(b'|', 1)

        # length of user_id: 8
        assert len(user_id) == 8, user_id

        if user_id in state:
            local_socket = state[user_id]['local_socket']
        else:
            local_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            local_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
            local_socket.connect((LOCAL_HOST, LOCAL_PORT))
            state[user_id] = {
                'local_socket': local_socket,
            }

        client_logger.info('send to %s %s', local_socket, data)
        client_logger.debug('state %s', state)
        local_socket.sendall(data)


def read_from_local(client_socket):
    while True:

        umap = {}
        local_sockets = []
        for key, value in state.items():
            user_id = key
            local_socket = value['local_socket']
            umap[id(local_socket)] = user_id
            local_sockets.append(local_socket)

        if not local_sockets:
            time.sleep(0.5)
            continue

        s_read, _, _ = select.select(local_sockets, [], [], 0.5)

        for s in s_read:
            data = s.recv(BUFFER_SIZE)

            if data == b'':
                continue

            client_logger.info('recv from %s: %s', s, data)

            user_id = umap[id(s)]

            # <frpy>12345678|abcdef1234</frpy>
            # external length: 22
            data = b'<frpy>' + user_id + b'|' + data + b'</frpy>'
            client_socket.sendall(data)


# -------------------------------


print('======== frpy v0.0.1 ========')

args = sys.argv[1:]

if not args:
    print('Please chose the mode (1 or 2):')
    print('1. Server')
    print('2. Client')
    mode = input()
    if mode not in ['1', '2']:
        input('Just entry 1 or 2!')
        sys.exit()
    args = [mode]

if args[0] in ['1', 'server']:
    mode = 'server'
else:
    mode = 'client'

if len(args) > 1:
    SERVER_HOST = args[1]

if len(args) > 2:
    SERVER_PORT = int(args[2])

if len(args) > 3:
    LOCAL_HOST = args[3]

if len(args) > 4:
    LOCAL_PORT = int(args[4])

if len(args) > 5:
    REMOTE_PORT = int(args[5])

if len(args) > 6:
    BUFFER_SIZE = int(args[6])

if mode == 'server':

    if len(args) > 3:
        BUFFER_SIZE = int(args[3])

    client_logger.info('frpy server')
    client_logger.info('SERVER_HOST: %s', SERVER_HOST)
    client_logger.info('SERVER_PORT: %s', SERVER_PORT)
    client_logger.info('BUFFER_SIZE: %s', BUFFER_SIZE)

    server = MainServer(host=SERVER_HOST, port=SERVER_PORT, buffer_size=BUFFER_SIZE)
    server.run()

else:

    client_logger.info('======== frpy client ========')
    client_logger.info('SERVER_HOST: %s', SERVER_HOST)
    client_logger.info('SERVER_PORT: %s', SERVER_PORT)
    client_logger.info('LOCAL_HOST: %s', LOCAL_HOST)
    client_logger.info('LOCAL_PORT: %s', LOCAL_PORT)
    client_logger.info('REMOTE_PORT: %s', REMOTE_PORT)
    client_logger.info('BUFFER_SIZE: %s', BUFFER_SIZE)

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_socket.connect((SERVER_HOST, SERVER_PORT))

    msg = '<frpy>00000000|%d</frpy>' % REMOTE_PORT
    client_socket.sendall(msg.encode())

    start_new_thread(write_to_local, (client_socket,), {})
    start_new_thread(read_from_local, (client_socket,), {})

    while True:
        client_logger.debug(threading.enumerate())
        time.sleep(10)
