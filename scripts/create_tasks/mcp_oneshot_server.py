#!/usr/bin/env python3
"""
MCP server for Codex-in-the-Box task creation.
Provides start-task and end-task tools for capturing coding sessions.
"""

import json
import sys
import asyncio
import logging
import tempfile
import os
from pathlib import Path
from typing import Dict, Any

# Ensure src is on sys.path to import package modules
from pathlib import Path as _PathForSys
_REPO_ROOT = _PathForSys(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / 'src'
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Try to import MCP SDK, fall back to JSON-RPC if not available
try:
    from mcp.server import Server
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server
    from mcp.types import ServerCapabilities, TextContent, Tool, ToolsCapability
    HAS_MCP_SDK = True
except ImportError:
    HAS_MCP_SDK = False

# Use user-specific log directory or temp directory
_log_dir = Path.home() / ".oneshot" / "logs"
try:
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_file = _log_dir / "mcp_server.out"
except (OSError, PermissionError):
    # Fall back to temp directory if home directory is not writable
    _log_file = Path(tempfile.gettempdir()) / "oneshot_mcp_server.out"

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(str(_log_file)),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)
logger.info(f"MCP server log file: {_log_file}")

# Import OneShot implementation from package
from one_shot.task_creation import OneShotTaskManager, WorktreeReadiness  # noqa: E402

# MCP SDK Implementation (if available)
if HAS_MCP_SDK:
    # Initialize the task manager with custom paths from environment if set
    import os
    from pathlib import Path
    base_dir = os.environ.get('ONESHOT_BASE_DIR')
    tasks_dir = os.environ.get('ONESHOT_TASKS_DIR')
    task_manager = OneShotTaskManager(
        base_dir=Path(base_dir) if base_dir else None,
        tasks_dir=Path(tasks_dir) if tasks_dir else None
    )
    
    # Create the MCP server
    server = Server("oneshot")
    
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available tools"""
        return [
            Tool(
                name="repo_start_task_v1",
                description="Start a new OneShot task",
                inputSchema={
                    "type": "object",
                    "required": ["task_title"],
                    "properties": {
                        "task_title": {
                            "type": "string",
                            "description": "Title of the task"
                        },
                        "notes": {
                            "type": "string",
                            "description": "Additional notes",
                            "default": ""
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Task labels",
                            "default": []
                        }
                    }
                }
            ),
            Tool(
                name="repo_end_task_v1",
                description="End the current OneShot task",
                inputSchema={
                    "type": "object",
                    "required": ["summary"],
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Task summary"
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Additional labels",
                            "default": []
                        }
                    }
                }
            ),
            Tool(
                name="repo_check_readiness_v1",
                description="Check worktree readiness",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            Tool(
                name="repo_autofix_readiness_v1",
                description="Auto-fix worktree issues",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            )
        ]
    
    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        """Handle tool calls"""
        if arguments is None:
            arguments = {}
        
        logger.info(f"Tool call: {name}")
        logger.debug(f"Arguments: {json.dumps(arguments, indent=2)}")
        
        try:
            if name == "repo_start_task_v1":
                result = task_manager.start_task(
                    arguments.get("task_title", ""),
                    arguments.get("notes", ""),
                    arguments.get("labels", [])
                )
            elif name == "repo_end_task_v1":
                result = task_manager.end_task(
                    arguments.get("summary", ""),
                    arguments.get("labels", [])
                )
            elif name == "repo_check_readiness_v1":
                result = WorktreeReadiness.check_readiness()
            elif name == "repo_autofix_readiness_v1":
                result = WorktreeReadiness.autofix_readiness()
            else:
                raise ValueError(f"Unknown tool: {name}")
            
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]
        except Exception as e:
            logger.error(f"Tool execution failed: {str(e)}")
            raise
    
    async def run_mcp_sdk():
        """Run using MCP SDK"""
        logger.info("Starting MCP server with SDK")
        async with stdio_server() as (read_stream, write_stream):
            init_options = InitializationOptions(
                server_name="oneshot",
                server_version="1.0.0",
                capabilities=ServerCapabilities(tools=ToolsCapability())
            )
            await server.run(read_stream, write_stream, init_options)

# JSON-RPC Protocol Implementation (fallback)
class MCPServer:
    """MCP stdio server implementation using JSON-RPC"""
    
    def __init__(self):
        import os
        from pathlib import Path
        base_dir = os.environ.get('ONESHOT_BASE_DIR')
        tasks_dir = os.environ.get('ONESHOT_TASKS_DIR')
        self.task_manager = OneShotTaskManager(
            base_dir=Path(base_dir) if base_dir else None,
            tasks_dir=Path(tasks_dir) if tasks_dir else None
        )
        self.version = "1.0.0"
    
    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle incoming MCP request"""
        method = request.get('method', '')
        params = request.get('params', {})
        request_id = request.get('id')
        
        logger.debug(f"Handling request: {method}")
        
        try:
            if method == 'initialize':
                return self.handle_initialize(request_id)
            elif method == 'notifications/initialized':
                # Just acknowledge the notification
                return None
            elif method == 'tools/list':
                return self.handle_list_tools(request_id)
            elif method == 'tools/call':
                return self.handle_tool_call(request_id, params)
            else:
                return self.error_response(request_id, -32601, f"Method not found: {method}")
        except Exception as e:
            logger.error(f"Error handling request: {str(e)}")
            return self.error_response(request_id, -32603, str(e))
    
    def handle_initialize(self, request_id: Any) -> Dict[str, Any]:
        """Handle initialize request"""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "oneshot-mcp-server",
                    "version": self.version
                }
            }
        }
    
    def handle_list_tools(self, request_id: Any) -> Dict[str, Any]:
        """List available tools"""
        tools = [
            {
                "name": "repo_start_task_v1",
                "description": "Start a new OneShot task",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_title": {"type": "string", "description": "Title of the task"},
                        "notes": {"type": "string", "description": "Additional notes"},
                        "labels": {"type": "array", "items": {"type": "string"}, "description": "Task labels"}
                    },
                    "required": ["task_title"]
                }
            },
            {
                "name": "repo_end_task_v1",
                "description": "End the current OneShot task",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Task summary"},
                        "labels": {"type": "array", "items": {"type": "string"}, "description": "Additional labels"}
                    },
                    "required": ["summary"]
                }
            },
            {
                "name": "repo_check_readiness_v1",
                "description": "Check worktree readiness",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "repo_autofix_readiness_v1",
                "description": "Automatically fix worktree issues",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]
        
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": tools
            }
        }
    
    def handle_tool_call(self, request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tool call"""
        tool_name = params.get('name', '')
        arguments = params.get('arguments', {})
        
        logger.info(f"Tool call: {tool_name}")
        logger.debug(f"Arguments: {json.dumps(arguments, indent=2)}")
        
        try:
            if tool_name in ['repo_start_task', 'repo_start_task_v1']:
                result = self.task_manager.start_task(
                    arguments['task_title'],
                    arguments.get('notes', ''),
                    arguments.get('labels', [])
                )
            elif tool_name in ['repo_end_task', 'repo_end_task_v1']:
                result = self.task_manager.end_task(
                    arguments['summary'],
                    arguments.get('labels', [])
                )
            elif tool_name in ['repo_check_readiness', 'repo_check_readiness_v1']:
                result = WorktreeReadiness.check_readiness()
            elif tool_name in ['repo_autofix_readiness', 'repo_autofix_readiness_v1']:
                result = WorktreeReadiness.autofix_readiness()
            else:
                return self.error_response(request_id, -32602, f"Unknown tool: {tool_name}")
            
            # Return successful result
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, indent=2)
                        }
                    ]
                }
            }
            logger.debug(f"Returning success response: {json.dumps(response, indent=2)}")
            return response
        except Exception as e:
            logger.error(f"Tool execution failed: {str(e)}")
            return self.error_response(request_id, -32603, str(e))
    
    def error_response(self, request_id: Any, code: int, message: str) -> Dict[str, Any]:
        """Create error response"""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message
            }
        }
    
    def run(self):
        """Run the MCP server"""
        logger.info("Starting MCP server (stdio mode)")
        
        while True:
            try:
                # Read from stdin
                line = sys.stdin.readline()
                if not line:
                    break
                
                # Parse JSON-RPC request
                request = json.loads(line)
                
                # Handle request
                response = self.handle_request(request)
                
                # Write response to stdout (only if not a notification)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + '\n')
                    sys.stdout.flush()
                
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
            except KeyboardInterrupt:
                logger.info("Server interrupted")
                break
            except Exception as e:
                logger.error(f"Server error: {str(e)}")

def main():
    """Main entry point"""
    if HAS_MCP_SDK:
        # Try to use MCP SDK
        try:
            asyncio.run(run_mcp_sdk())
        except Exception as e:
            logger.error(f"MCP SDK failed: {e}, falling back to JSON-RPC")
            server = MCPServer()
            server.run()
    else:
        # Use JSON-RPC implementation
        server = MCPServer()
        server.run()

if __name__ == '__main__':
    main()
