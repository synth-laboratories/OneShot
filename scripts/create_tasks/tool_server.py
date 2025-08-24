#!/usr/bin/env python3
"""
HTTP fallback server for OneShot task creation.
Provides the same functionality as the MCP server but via HTTP endpoints.
"""

import json
import sys
import os
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import argparse

# Ensure src is on sys.path to import package modules
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / 'src'
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from one_shot.task_creation import OneShotTaskManager, WorktreeReadiness

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/oneshot_tool_server.out'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)

class OneShotHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OneShot operations"""
    
    def __init__(self, *args, **kwargs):
        self.task_manager = OneShotTaskManager()
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests"""
        if self.path == '/health':
            self.send_json_response(200, {"status": "healthy", "service": "oneshot-tool-server"})
        else:
            self.send_error(404, "Not Found")
    
    def do_POST(self):
        """Handle POST requests"""
        try:
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else b'{}'
            
            # Parse JSON
            try:
                data = json.loads(body.decode('utf-8'))
            except json.JSONDecodeError as e:
                self.send_json_response(400, {
                    "ok": False,
                    "code": "INVALID_JSON",
                    "message": f"Invalid JSON: {str(e)}"
                })
                return
            
            # Route to appropriate handler
            if self.path == '/start-task':
                self.handle_start_task(data)
            elif self.path == '/end-task':
                self.handle_end_task(data)
            elif self.path == '/check-readiness':
                self.handle_check_readiness(data)
            elif self.path == '/autofix-readiness':
                self.handle_autofix_readiness(data)
            else:
                self.send_error(404, "Not Found")
                
        except Exception as e:
            logger.error(f"Request handling error: {str(e)}", exc_info=True)
            self.send_json_response(500, {
                "ok": False,
                "code": "INTERNAL_ERROR",
                "message": str(e)
            })
    
    def handle_start_task(self, data: dict):
        """Handle start-task request"""
        # Validate required fields
        if 'task_title' not in data:
            self.send_json_response(400, {
                "ok": False,
                "code": "MISSING_FIELD",
                "message": "task_title is required"
            })
            return
        
        # Call task manager
        result = self.task_manager.start_task(
            task_title=data['task_title'],
            notes=data.get('notes', ''),
            labels=data.get('labels', [])
        )
        
        # Send response
        status_code = 200 if result.get('ok') else 400
        self.send_json_response(status_code, result)
    
    def handle_end_task(self, data: dict):
        """Handle end-task request"""
        # Validate required fields
        if 'summary' not in data:
            self.send_json_response(400, {
                "ok": False,
                "code": "MISSING_FIELD",
                "message": "summary is required"
            })
            return
        
        # Call task manager
        result = self.task_manager.end_task(
            summary=data['summary'],
            labels=data.get('labels', [])
        )
        
        # Send response
        status_code = 200 if result.get('ok') else 400
        self.send_json_response(status_code, result)
    
    def handle_check_readiness(self, data: dict):
        """Handle check-readiness request"""
        result = WorktreeReadiness.check_readiness()
        status_code = 200 if result.get('ok') else 400
        self.send_json_response(status_code, result)
    
    def handle_autofix_readiness(self, data: dict):
        """Handle autofix-readiness request"""
        result = WorktreeReadiness.autofix_readiness()
        status_code = 200 if result.get('ok') else 400
        self.send_json_response(status_code, result)
    
    def send_json_response(self, status_code: int, data: dict):
        """Send JSON response"""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode('utf-8'))
    
    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.info(f"HTTP: {format % args}")

def run_server(host: str = '127.0.0.1', port: int = 8080):
    """Run the HTTP server"""
    logger.info(f"Starting OneShot Tool Server on {host}:{port}")
    
    server = HTTPServer((host, port), OneShotHTTPHandler)
    
    logger.info(f"Server listening on http://{host}:{port}")
    logger.info("Endpoints:")
    logger.info("  GET  /health           - Health check")
    logger.info("  POST /start-task       - Start a new task")
    logger.info("  POST /end-task         - End the current task")
    logger.info("  POST /check-readiness  - Check worktree readiness")
    logger.info("  POST /autofix-readiness - Auto-fix worktree issues")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
    except Exception as e:
        logger.error(f"Server error: {str(e)}", exc_info=True)
    finally:
        server.shutdown()
        logger.info("Server shutdown complete")

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='OneShot HTTP Tool Server')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8080, help='Port to listen on')
    args = parser.parse_args()
    
    run_server(args.host, args.port)

if __name__ == '__main__':
    main()