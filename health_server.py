#!/usr/bin/env python3
"""
Servidor HTTP simples para health checks em serviços cloud
Roda junto com o monitor BID em uma thread separada
"""

import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests for health checks"""
        if self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            status = {
                'status': 'healthy',
                'timestamp': datetime.now().isoformat(),
                'service': 'bid-monitor',
                'uptime': time.time() - getattr(self.server, 'start_time', time.time())
            }
            
            self.wfile.write(json.dumps(status).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress default HTTP server logs"""
        pass

class HealthServer:
    def __init__(self, port=8080):
        self.port = port
        self.server = None
        self.thread = None
        self.running = False
    
    def start(self):
        """Start the health check server in a separate thread"""
        if self.running:
            return
        
        try:
            self.server = HTTPServer(('0.0.0.0', self.port), HealthCheckHandler)
            self.server.start_time = time.time()
            
            self.thread = threading.Thread(target=self._run_server, daemon=True)
            self.thread.start()
            
            self.running = True
            logger.info(f"Health check server started on port {self.port}")
            
        except Exception as e:
            logger.error(f"Failed to start health server: {e}")
    
    def _run_server(self):
        """Run the server (called in separate thread)"""
        try:
            self.server.serve_forever()
        except Exception as e:
            logger.error(f"Health server error: {e}")
            self.running = False
    
    def stop(self):
        """Stop the health check server"""
        if not self.running:
            return
        
        try:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
            
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=5)
            
            self.running = False
            logger.info("Health check server stopped")
            
        except Exception as e:
            logger.error(f"Error stopping health server: {e}")

# Global health server instance
health_server = HealthServer()

def start_health_server():
    """Start the global health server"""
    health_server.start()

def stop_health_server():
    """Stop the global health server"""
    health_server.stop()

if __name__ == "__main__":
    # Test the health server
    start_health_server()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_health_server()