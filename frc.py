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


state = {}


def write_to_local(client_socket):
    while True:
        s_read, _, _ = select.select([client_socket], [], [], 0.5)
        if not s_read:
            continue

        s = s_read[0]
        data = s.recv(8 + BUFFER_SIZE)

        user_id, data = data.split(b'|', 1)
        if user_id in state:
            local_socket = state[user_id]['local_socket']
        else:
            local_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            local_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
            local_socket.connect((LOCAL_HOST, LOCAL_PORT))
            state[user_id] = {
                'local_socket': local_socket,
            }

        logging.info('send to', local_socket, data)
        logging.info('state', state)
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

            logging.info('recv from', s, data)

            user_id = umap[id(s)]

            data = user_id + b'|' + data
            client_socket.sendall(data)


client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.connect((SERVER_HOST, SERVER_PORT))
msg = '0000000|%d' % REMOTE_PORT
client_socket.sendall(msg.encode())


start_new_thread(write_to_local, (client_socket, ), {})
start_new_thread(read_from_local, (client_socket, ), {})


logging.info('=== start frpy client ===')
while True:
    time.sleep(10)
