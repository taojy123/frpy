import logging
import select
import socket
import time
from _thread import start_new_thread


# =========== Conf ===========
SERVER_HOST = '127.0.0.1'
SERVER_PORT = 8000
REMOTE_PORT = 60080
LOCAL_HOST = 'tslow.cn'
LOCAL_PORT = 2048
BUFFER_SIZE = 1024
# =============================


logging.basicConfig(level=logging.DEBUG)
client_logger = logging.getLogger('Client')
state = {}


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

            client_logger.info('recv from', s, data)

            user_id = umap[id(s)]

            # <frpy>12345678|abcdef1234</frpy>
            # external length: 22
            data = b'<frpy>' + user_id + b'|' + data + b'</frpy>'
            client_socket.sendall(data)


client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.connect((SERVER_HOST, SERVER_PORT))
msg = '<frpy>00000000|%d</frpy>' % REMOTE_PORT
client_socket.sendall(msg.encode())


start_new_thread(write_to_local, (client_socket, ), {})
start_new_thread(read_from_local, (client_socket, ), {})


client_logger.info('=== start frpy client ===')
while True:
    time.sleep(10)
