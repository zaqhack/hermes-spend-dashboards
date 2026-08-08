    _GRAFANA_TOKEN = None
    
    def _grafana_token(self) -> str:
        """Load Grafana service account token from secrets file (cached)."""
        if self._GRAFANA_TOKEN is None:
            try:
                token_path = os.path.expanduser('~/.hermes/secrets/grafana-proxy-token')
                with open(token_path) as f:
                    self.__class__._GRAFANA_TOKEN = f.read().strip()
            except Exception:
                self.__class__._GRAFANA_TOKEN = ''
        return self._GRAFANA_TOKEN or ''
    
    def _proxy_to_grafana(self, parsed) -> bool:
        """Proxy /grafana-proxy/* or /public/* requests to the LAN Grafana instance."""
        path = parsed.path
        if not path.startswith('/grafana-proxy') and not path.startswith('/public/') and not path.startswith('/apis/') and not path.startswith('/avatar/'):
            return False
        
        import http.client
        
        # Determine the sub-path to forward to nginx/Grafana
        # nginx expects the full /grafana-proxy/* path for subpath rewriting
        if path.startswith('/grafana-proxy'):
            subpath = path  # keep full path for nginx
        else:
            subpath = path  # /public/... goes as-is
        if not subpath:
            subpath = '/'
        if parsed.query:
            subpath += '?' + parsed.query
        
        grafana_host = '192.168.49.15'
        grafana_port = 3001
        
        try:
            conn = http.client.HTTPConnection(grafana_host, grafana_port, timeout=15)
            
            # Build forwarded headers
            fwd_headers = {}
            for key, val in self.headers.items():
                kl = key.lower()
                if kl in ('host', 'connection', 'transfer-encoding',
                          'keep-alive', 'upgrade', 'proxy-connection',
                          'accept-encoding'):
                    continue
                fwd_headers[key] = val
            fwd_headers['Accept-Encoding'] = 'identity'
            fwd_headers['Host'] = f'{grafana_host}:{grafana_port}'
            fwd_headers['X-Forwarded-Host'] = self.headers.get('Host', 'localhost')
            fwd_headers['X-Forwarded-Proto'] = ('https' if getattr(self, 'requestline', '').startswith('HTTPS')
                                                 else 'http')
            # Inject Grafana service account token (Viewer role)
            token = self._grafana_token()
            if token:
                fwd_headers['Authorization'] = 'Bearer ' + token
            
            # Read request body if present
            body = None
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                body = self.rfile.read(content_length)
            
            conn.request(self.command, subpath, body=body, headers=fwd_headers)
            resp = conn.getresponse()
            
            # Read response body
            resp_body = resp.read()
            
            # Rewrite base href in HTML responses so Grafana API calls go through the proxy
            content_type = ''
            for key, val in resp.getheaders():
                if key.lower() == 'content-type':
                    content_type = val
                    break
            if ('text/html' in content_type and b'<base href=' in resp_body):
                old_base = b'<base href="/">'
                new_base = b'<base href="/grafana-proxy/">'
                if old_base in resp_body:
                    resp_body = resp_body.replace(old_base, new_base)
                else:
                    import re as _re
                    resp_body = _re.sub(
                        b'<base[^>]*href=["\x27]([^"\x27]*)["\x27][^>]*>',
                        b'<base href="/grafana-proxy/">',
                        resp_body,
                    )
            
            # Send response back to client
            self.send_response(resp.status, resp.reason)
            
            # Rewrite response body: change Grafana's base href to go through proxy
            content_type = ''
            for key, val in resp.getheaders():
                if key.lower() == 'content-type':
                    content_type = val
                    break
            
            # Rewrite base href in HTML responses so Grafana's API/asset calls use the proxy path
            if resp.status == 200 and 'text/html' in content_type:
                body_str = resp_body.decode('utf-8', errors='replace')
                body_str = body_str.replace('<base href="/"', '<base href="/grafana-proxy/"')
                body_str = body_str.replace("<base href='/'", "<base href='/grafana-proxy/'")
                resp_body = body_str.encode('utf-8')
            
            # Forward response headers (with rewrites)
            for key, val in resp.getheaders():
                kl = key.lower()
                if kl in ('transfer-encoding', 'connection', 'keep-alive',
                          'upgrade', 'proxy-connection', 'content-length'):
                    continue
                # Rewrite Location headers (redirects) through proxy
                if kl == 'location':
                    if val.startswith('/'):
                        val = '/grafana-proxy' + val
                    elif grafana_host in val:
                        val = re.sub(r'https?://[^/]+', '', val)
                        if val.startswith('/'):
                            val = '/grafana-proxy' + val
                # Strip frame-ancestors so it can render in the WebUI iframe.
                # Also handle Content-Security-Policy-Report-Only: Zen/Firefox
                # honours report-only frame-ancestors for framing decisions,
                # and its report-uri would spam the wrong endpoint anyway.
                if kl in ('content-security-policy', 'content-security-policy-report-only'):
                    val = re.sub(r'frame-ancestors[^;]*;?', '', val)
                # Remove X-Frame-Options so it can be iframed
                if kl == 'x-frame-options':
                    continue
                # Rewrite Set-Cookie for proxy path
                if kl == 'set-cookie':
                    val = re.sub(r';\\s*domain\\s*=[^;]+', '', val, flags=re.IGNORECASE)
                    val = re.sub(r';\\s*path\\s*=[^;]+', '; Path=/grafana-proxy', val, flags=re.IGNORECASE)
                    val = re.sub(r';\\s*samesite\\s*=\\s*strict', '; SameSite=Lax', val, flags=re.IGNORECASE)
                self.send_header(key, val)
            
            # Body length is known (we read it fully above) — send it explicitly.
            # Without Content-Length (or chunked), HTTP/1.1 keep-alive clients
            # wait for connection close to detect end-of-body, which hangs
            # iframe/asset loads through the proxy.
            self.send_header('Content-Length', str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
            conn.close()
            return True
        except Exception as exc:
            import traceback
            self._safe_webui_print(f'[webui] Grafana proxy error: {exc}\n' + traceback.format_exc())
            from api.helpers import j
            return j(self, {'error': f'Grafana proxy error: {exc}'}, status=502)
