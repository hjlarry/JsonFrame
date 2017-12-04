import socket
import sys
from io import BytesIO
from urllib import parse
import asyncio


loop = asyncio.get_event_loop()


class WSGIServer(object):
    addr_family = socket.AF_INET
    socket_type = socket.SOCK_STREAM
    request_queue_size = 1024
    server_version = 0.2

    def __init__(self, server_addr):
        self.listen_socket = listen_socket = socket.socket(self.addr_family, self.socket_type)
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_socket.setblocking(False)
        listen_socket.bind(server_addr)
        listen_socket.listen(self.request_queue_size)
        host, port = self.listen_socket.getsockname()[:2]
        self.servername = socket.getfqdn(host)
        self.port = port
        self.head_set = []
        self.headers = {}
        self.appication = None
        self.client_con = None
        self.client_addr = None
        self.request_method = None
        self.path = None
        self.request_version = None
        self.request_data = None

    def set_app(self, app):
        self.appication = app

    async def server_forever(self):
        listen_socket = self.listen_socket

        while True:
            try:
                self.client_con, self.client_addr = await loop.sock_accept(listen_socket)
                loop.create_task(self.handle_one_request())
            except Exception as e:
                print(e)

    async def handle_one_request(self):
        self.request_data = request_data = await loop.sock_recv(self.client_con, 1024)
        print(''.join('< {} \n'.format(line) for line in request_data.splitlines()))

        self.parse_request(request_data.decode())
        env = self.get_environ()
        result = self.appication(env, self.start_response)
        loop.create_task(self.finish_response(result))

    def parse_request(self, text):
        request_line = text.splitlines()
        first_line = request_line.pop(0).rstrip('\r\n')
        (self.request_method, self.path, self.request_version) = first_line.split()

        for header in request_line:
            header = header.split(': ')
            if len(header) < 2:
                continue
            self.headers[header[0]] = header[1]

    def get_environ(self):
        env = dict()
        env['wsgi.version'] = (1, 0)
        env['wsgi.url_scheme'] = 'http'
        env['wsgi.input'] = BytesIO(self.request_data)
        env['wsgi.errors'] = sys.stderr
        env['wsgi.multithread'] = False
        env['wsgi.multiprocess'] = False
        env['wsgi.run_once'] = False

        env['REQUEST_METHOD'] = self.request_method
        env['SERVER_NAME'] = self.servername
        env['SERVER_PORT'] = str(self.port)
        env['GATEWAY_INTERFACE'] = 'CGI/1.1'
        env['REMOTE_HOST'] = self.servername
        env['CONTENT_LENGTH'] = ''
        env['SCRIPT_NAME'] = ''

        env['SERVER_PROTOCOL'] = self.request_version
        env['SERVER_SOFTWARE'] = self.server_version

        if '?' in self.path:
            path, query = self.path.split('?', 1)
        else:
            path, query = self.path, ''

        env['PATH_INFO'] = parse.unquote(path)
        env['QUERY_STRING'] = query

        length = self.headers.get('content-length')
        if length:
            env['CONTENT_LENGTH'] = length

        for k, v in self.headers.items():
            k = k.replace('-', '_').upper()
            v = v.strip()
            if k in env:
                continue  # skip content length, type,etc.
            if 'HTTP_' + k in env:
                env['HTTP_' + k] += ',' + v  # comma-separate multiple headers
            else:
                env['HTTP_' + k] = v
        return env

    def start_response(self, status, response_headers):
        server_headers = [('Date', 'Tue, 2017 GMT'), ('Server', 'WSGIServer 1.0')]
        self.head_set = [status, response_headers + server_headers]

    async def finish_response(self, result):
        try:
            status, response_headers = self.head_set
            response = 'HTTP/1.1 {status}\r\n'.format(status=status)
            for header in response_headers:
                response += '{0}: {1}\r\n'.format(*header)
            response += '\r\n'
            for data in result:
                response += data.decode()
            print(''.join('> {} \n'.format(line) for line in response.splitlines()))
            await loop.sock_sendall(self.client_con, str.encode(response))
        finally:
            self.client_con.close()


SERVERADDR = (HOST, PORT) = '', 8081


def server_run(server_addr, app):
    server = WSGIServer(server_addr)
    server.set_app(app)
    try:
        loop.run_until_complete(server.server_forever())
    except Exception as e:
        print(e)
    finally:
        loop.close()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('Provide a wsgi app object')
    app_path = sys.argv[1]
    mod, application = app_path.split(':')
    mod = __import__(mod)
    application = getattr(mod, application)
    print('WSGI start {}'.format(PORT))
    server_run(SERVERADDR, application)
