import logging
import random
import socket
import threading
import time
from _thread import start_new_thread


SERVER_PORT = 33333
STATE = {}

logging.basicConfig(level=logging.DEBUG)


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
        logging.info('[start tcp server] on %s:%d', self.host, self.port)
        try:
            while not self.stopped:
                client_socket, client_address = self.server_socket.accept()
                start_new_thread(self.handle_client, (client_socket, client_address), {})
                self.client_sockets.append(client_socket)
        except OSError as e:
            logging.debug(e)
        logging.info('[finish tcp server] on %s:%d', self.host, self.port)

    def shutdown(self):
        self.stopped = True
        for s in self.client_sockets:
            s.close()
        self.server_socket.close()
        logging.info('[shutdown tcp server] on %s:%d', self.host, self.port)

    def handle_client(self, client_socket, client_address):
        logging.info('[new client] on %s:%d, client address: %s', self.host, self.port, client_address)
        while True:
            data = client_socket.recv(self.buffer_size)
            self.handle_recv(client_socket, data)
            if data == b'':
                break
        self.client_sockets.remove(client_socket)
        client_socket.close()
        logging.info('[close client] on %s:%d, client address: %s', self.host, self.port, client_address)

    def handle_recv(self, client_socket, data):
        # should be override
        logging.debug(data)
        client_socket.sendall(data[::-1])
        if not data:
            self.shutdown()


class WorkerServer(EasyTcpServer):

    def __init__(self, host='0.0.0.0', port=8000, buffer_size=1024, parent_socket=None, main_server=None):
        assert parent_socket, 'parent]_socket must been set'
        assert main_server, 'main_server must been set'
        self.parent_socket = parent_socket
        self.main_server = main_server
        super().__init__(host, port, buffer_size)

    def handle_recv(self, client_socket, data):
        logging.debug('data from user: %s', data)
        user_socket = client_socket
        user_id = random_id()
        data = user_id + b'|' + data
        self.parent_socket.sendall(data)
        self.main_server.state[user_id] = {
            'parent_socket': self.parent_socket,
            'user_socket': user_socket,
        }


class MainServer(EasyTcpServer):

    workers = []
    state = {}

    def __init__(self, host='0.0.0.0', port=8000, buffer_size=1024):
        super().__init__(host, port, 8 + buffer_size)

    def split_data(self, data):
        assert b'|' in data, 'recv error data: %s' % data
        user_id, data = data.split(b'|', 1)
        assert len(user_id) == 7, 'recv error data: %s' % data
        return user_id, data


    def make_new_worker(self, port, client_socket):
        # make a new server in a new thread. [async]
        kwargs = {
            'remote_port': port,
            'parent_socket': client_socket,
        }
        start_new_thread(self.start_worker_server, (), kwargs)

    def start_worker_server(self, remote_port, parent_socket):
        # parent_socket is a socket between frpy server and frpy client
        worker_server = WorkerServer(port=remote_port, parent_socket=parent_socket, main_server=self)
        self.workers.append(worker_server)
        worker_server.run()

    def handle_recv(self, client_socket, data):
        # todo: try and no raise
        logging.debug('data from client: %s', data)
        user_id, data = self.split_data(data)
        if user_id == b'0000000':
            port = int(data.decode())
            self.make_new_worker(port, client_socket)
            return

        user_socket = self.state.get(user_id, {}).get('user_socket')
        if not user_socket:
            logging.warning('#%s %s not found!', user_id, client_socket)
            logging.warning('state: %s', self.state)
            return

        user_socket.sendall(data)

        # todo: improve and fix it
        time.sleep(0.3)


exists = set()


def random_id():
    # return: bytes
    n = random.randint(1000000, 9999999)
    for i in range(1000000):
        if n in exists:
            continue
        exists.add(n)
        return str(n).encode()
    assert False, 'random_ids is full!'


server = MainServer()
server.run()

