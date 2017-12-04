# A simple Json Web Server
import re
import json
# from socket_server import WSGIServer
from asynic_server import server_run
from urllib import parse


class BaseHttpServer(object):
    def __init__(self):
        self.routes = []
        self.router = Router()
        self.request_method = None
        self.path = ''

    def __call__(self, env, start_response):
        return self.wsgi_app(env, start_response)

    def wsgi_app(self, env, start_response):
        self.request_method = env['REQUEST_METHOD']
        self.path = env['PATH_INFO']
        try:
            route, args = self.router.match(env)
            output = route.call(**args)
        except HttpErrorResponse as e:
            output = e

        if isinstance(output, str):
            response = Response(body=output)
        elif isinstance(output, dict):
            response = Response(body=json.dumps(output))
        elif isinstance(output, Response):
            response = output
        else:
            response = HttpErrorResponse(500, self.path, 'unknown error')
        start_response(response.status, response.response_header)
        return response.content()

    def route(self, path=None, method='GET', callback=None, name=None):
        if callable(path):
            path, callback = None, path

        def decorator(fn):
            for rule in make_list(path):
                for verb in make_list(method):
                    verb = verb.upper()
                    route = Route(self, rule, verb, fn, name=name)
                    self.add_route(route)
            return fn
        return decorator(callback) if callback else decorator

    def add_route(self, route):
        self.routes.append(route)
        self.router.add(route.rule, route.method, route, name=route.name)

    def run(self, host='', port=8081):
        server_addr = (host, port)
        server_run(server_addr, self)


def make_list(data):  # This is just to handy
    if isinstance(data, (tuple, list, set, dict)):
        return list(data)
    elif data:
        return [data]
    else:
        return []


def _re_flatten(p):
    """ Turn all capturing groups in a regular expression pattern into
        non-capturing groups. """
    if '(' not in p:
        return p
    return re.sub(r'(\\*)(\(\?P<[^>]+>|\((?!\?))', lambda m: m.group(0) if len(m.group(1)
                                                                               ) % 2 else m.group(1) + '(?:', p)


class Router(object):
    default_pattern = '[^/]+'
    default_filter = 're'

    def __init__(self, strict=False):
        self.rules = []  # All rules in order
        self._groups = {}  # index of regexes to find them in dyna_routes
        self.builder = {}  # Data structure for the url builder
        self.static = {}  # Search structure for static routes
        self.dyna_routes = {}
        self.dyna_regexes = {}  # Search structure for dynamic routes
        #: If true, static routes are no longer checked first.
        self.strict_order = strict
        self.filters = {
            're': lambda conf:
            (_re_flatten(conf or self.default_pattern), None, None),
            'int': lambda conf: (r'-?\d+', int, lambda x: str(int(x))),
            'float': lambda conf: (r'-?[\d.]+', float, lambda x: str(float(x))),
            'path': lambda conf: (r'.+?', None, None)}

    rule_syntax = re.compile('(\\\\*)' 
                             '(?:(?::([a-zA-Z_][a-zA-Z_0-9]*)?( )(?:#(.*?)#)?)' 
                             '|(?:<([a-zA-Z_][a-zA-Z_0-9]*)?(?::([a-zA-Z_]*)' 
                             '(?::((?:\\\\.|[^\\\\>]+)+)?)?)?>))')

    def add_filter(self, name, func):
        """ Add a filter. The provided function is called with the configuration
        string as parameter and must return a (regexp, to_python, to_url) tuple.
        The first element is a string, the last two are callables or None. """
        self.filters[name] = func

    def _itertokens(self, rule):
        offset, prefix = 0, ''
        for match in self.rule_syntax.finditer(rule):
            prefix += rule[offset:match.start()]
            g = match.groups()
            if len(g[0]) % 2:  # Escaped wildcard
                prefix += match.group(0)[len(g[0]):]
                offset = match.end()
                continue
            if prefix:
                yield prefix, None, None
            name, filtr, conf = g[4:7] if g[2] is None else g[1:4]
            yield name, filtr or 'default', conf or None
            offset, prefix = match.end(), ''
        if offset <= len(rule) or prefix:
            yield prefix + rule[offset:], None, None

    def add(self, rule, method, target, name=None):
        """ Add a new rule or replace the target for an existing rule. """
        anons = 0    # Number of anonymous wildcards found
        keys = []   # Names of keys
        pattern = ''   # Regular expression pattern with named groups
        filters = []   # Lists of wildcard input filters
        builder = []   # Data structure for the URL builder
        is_static = True

        for key, mode, conf in self._itertokens(rule):
            if mode:
                is_static = False
                if mode == 'default':
                    mode = self.default_filter
                mask, in_filter, out_filter = self.filters[mode](conf)
                if not key:
                    pattern += '(?:%s)' % mask
                    key = 'anon%d' % anons
                    anons += 1
                else:
                    pattern += '(?P<%s>%s)' % (key, mask)
                    keys.append(key)
                if in_filter:
                    filters.append((key, in_filter))
                builder.append((key, out_filter or str))
            elif key:
                pattern += re.escape(key)
                builder.append((None, key))

        self.builder[rule] = builder
        if name:
            self.builder[name] = builder

        if is_static and not self.strict_order:
            self.static.setdefault(method, {})
            self.static[method][self.build(rule)] = (target, None)
            return

        try:
            re_pattern = re.compile('^(%s)$' % pattern)
            re_match = re_pattern.match
        except re.error:
            raise Exception("Could not add Route: %s " % rule)

        if filters:
            def getargs(path):
                url_args = re_match(path).groupdict()
                for u_name, wildcard_filter in filters:
                    try:
                        url_args[u_name] = wildcard_filter(url_args[u_name])
                    except ValueError:
                        raise HttpErrorResponse(400, path, 'Path has wrong format.')
                return url_args
        elif re_pattern.groupindex:
            def getargs(path):
                return re_match(path).groupdict()
        else:
            getargs = None

        flatpat = _re_flatten(pattern)
        whole_rule = (rule, flatpat, target, getargs)

        if (flatpat, method) in self._groups:
            self.dyna_routes[method][self._groups[flatpat, method]] = whole_rule
        else:
            self.dyna_routes.setdefault(method, []).append(whole_rule)
            self._groups[flatpat, method] = len(self.dyna_routes[method]) - 1

        self._compile(method)

    def match(self, environ):
        """ Return a (target, url_agrs) tuple or raise HTTPError(400/404/405). """
        verb = environ['REQUEST_METHOD'].upper()
        path = environ['PATH_INFO'] or '/'
        methods = ['PROXY', verb, 'ANY']

        for method in methods:
            if method in self.static and path in self.static[method]:
                target, getargs = self.static[method][path]
                return target, getargs(path) if getargs else {}
            elif method in self.dyna_regexes:
                for combined, rules in self.dyna_regexes[method]:
                    match = combined(path)
                    if match:
                        target, getargs = rules[match.lastindex - 1]
                        return target, getargs(path) if getargs else {}

        # No matching route found. Collect alternative methods for 405 response
        allowed = set([])
        nocheck = set(methods)
        for method in set(self.static) - nocheck:
            if path in self.static[method]:
                allowed.add(verb)
        for method in set(self.dyna_regexes) - allowed - nocheck:
            for combined, rules in self.dyna_regexes[method]:
                match = combined(path)
                if match:
                    allowed.add(method)
        if allowed:
            raise HttpErrorResponse(405, path, 'Method not allowed.')

        # No matching route and no alternative method found. We give up
        # raise HTTPError(404, "Not found: " + repr(path))
        raise HttpErrorResponse(404, path, 'Response not Found.')

    def _compile(self, method):
        all_rules = self.dyna_routes[method]
        comborules = self.dyna_regexes[method] = []
        maxgroups = 99
        for x in range(0, len(all_rules), maxgroups):
            some = all_rules[x:x+maxgroups]
            combined = (flatpat for (_, flatpat, _, _) in some)
            combined = '|'.join('(^%s$)' % flatpat for flatpat in combined)
            combined = re.compile(combined).match
            rules = [(target, getargs) for (_, _, target, getargs) in some]
            comborules.append((combined, rules))

    def build(self, _name, *anons, **query):
        """ Build an URL by filling the wildcards in a rule. """
        builder = self.builder.get(_name)
        if not builder:
            raise Exception("No route with that name.", _name)
        try:
            for i, value in enumerate(anons):
                query['anon%d' % i] = value
            url = ''.join([f(query.pop(n)) if n else f for (n, f) in builder])
            return url if not query else url+'?'+parse.urlencode(query)
        except KeyError:
            raise Exception('Missing URL argument: ')


class CachedProperty(object):
    """ A property that is only computed once per instance and then replaces
        itself with an ordinary attribute. Deleting the attribute resets the
        property. """

    def __init__(self, func):
        self.__doc__ = getattr(func, '__doc__')
        self.func = func

    def __get__(self, obj, cls):
        if obj is None:
            return self
        value = obj.__dict__[self.func.__name__] = self.func(obj)
        return value


class Route(object):
    def __init__(self, app, rule, method, callback, name=None):
        self.app = app
        self.rule = rule
        self.method = method
        self.callback = callback
        self.name = name or None

    def __call__(self, *a, **ka):
        return self.call(*a, **ka)

    @CachedProperty
    def call(self):
        return self.callback


class Response(object):
    def __init__(self, status_code=None, body=''):
        self.body = body
        self.status = status_code or '200 OK'
        self.response_header = [('Content-Type', 'application/json')]

    def content(self):
        return [self.body.encode()]

    def add_header(self, header):
        self.response_header.append(header)


class HttpErrorResponse(Response, Exception):
    def __init__(self, status, path, msg):
        super().__init__()
        self.status = status
        self.body = json.dumps({'error_code': status, 'error_request_path': path, 'message': msg})
