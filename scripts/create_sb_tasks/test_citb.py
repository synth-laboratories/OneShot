#!/usr/bin/env python3
"""
Test script for CITB task creation components.
Tests individual components and end-to-end workflow.
"""

import json
import os
import sys
import tempfile
import subprocess
import time
import unittest
from pathlib import Path
import shutil
import requests
from unittest.mock import patch, MagicMock

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from mcp_citb_server import (
    GitHelpers, 
    TraceExporter, 
    WorktreeReadiness, 
    CITBTaskManager,
    MCPServer
)

class TestGitHelpers(unittest.TestCase):
    """Test GitHelpers class"""
    
    def test_get_current_branch(self):
        """Test getting current branch"""
        branch = GitHelpers.get_current_branch()
        self.assertIsInstance(branch, str)
        self.assertTrue(len(branch) > 0)
    
    def test_get_head_sha(self):
        """Test getting HEAD SHA"""
        sha = GitHelpers.get_head_sha()
        self.assertIsInstance(sha, str)
        self.assertEqual(len(sha), 40)  # SHA-1 is 40 characters
    
    def test_get_remote_urls(self):
        """Test getting remote URLs"""
        urls = GitHelpers.get_remote_urls()
        self.assertIsInstance(urls, list)
        # May be empty if no remotes configured
    
    def test_stage_all(self):
        """Test staging all changes"""
        result = GitHelpers.stage_all()
        self.assertIsInstance(result, bool)

class TestWorktreeReadiness(unittest.TestCase):
    """Test WorktreeReadiness class"""
    
    def test_check_readiness(self):
        """Test readiness check"""
        result = WorktreeReadiness.check_readiness()
        self.assertIn('ok', result)
        self.assertIn('issues', result)
        self.assertIn('summary', result)
        self.assertIsInstance(result['issues'], list)
    
    def test_autofix_readiness(self):
        """Test autofix functionality"""
        result = WorktreeReadiness.autofix_readiness()
        self.assertIn('ok', result)
        self.assertIn('fixed', result)
        self.assertIn('remaining_issues', result)
        self.assertIsInstance(result['fixed'], list)

class TestCITBTaskManager(unittest.TestCase):
    """Test CITBTaskManager class"""
    
    def setUp(self):
        """Set up test environment"""
        self.manager = CITBTaskManager()
        self.test_dir = Path(tempfile.mkdtemp())
        
    def tearDown(self):
        """Clean up test environment"""
        # Clean up state file
        if self.manager.state_file.exists():
            self.manager.state_file.unlink()
        
        # Clean up test directory
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
    
    def test_generate_task_slug(self):
        """Test task slug generation"""
        slug = self.manager.generate_task_slug("Test Task Name!")
        self.assertIsInstance(slug, str)
        self.assertIn('test-task-name', slug)
        self.assertTrue(len(slug) > 0)
        # Check for timestamp suffix
        parts = slug.split('_')
        self.assertEqual(len(parts), 3)  # name_date_time
    
    def test_start_task(self):
        """Test starting a task"""
        result = self.manager.start_task(
            task_title="Test Task",
            notes="Test notes",
            labels=["test", "unit"]
        )
        
        self.assertIn('ok', result)
        
        if result['ok']:
            self.assertIn('task_slug', result)
            self.assertIn('start_commit', result)
            self.assertIn('started_at', result)
            
            # Check state file was created
            self.assertTrue(self.manager.state_file.exists())
            
            # Clean up
            self.manager.state_file.unlink()
    
    @patch('mcp_citb_server.TraceExporter.export_session')
    def test_end_task(self, mock_export):
        """Test ending a task"""
        # Mock trace export
        mock_export.return_value = {
            "session_id": "test_session",
            "traces": [],
            "count": 0
        }
        
        # First start a task
        start_result = self.manager.start_task(
            task_title="Test End Task",
            notes="Testing end functionality"
        )
        
        if not start_result['ok']:
            self.skipTest("Could not start task")
        
        # Then end it
        result = self.manager.end_task(
            summary="Test completed",
            labels=["completed"]
        )
        
        self.assertIn('ok', result)
        
        if result['ok']:
            self.assertIn('task_dir', result)
            self.assertIn('diff_bytes', result)
            self.assertIn('touched_files', result)
            self.assertIn('clean_trace_path', result)
            
            # Check state file was removed
            self.assertFalse(self.manager.state_file.exists())
            
            # Check task directory was created
            task_dir = Path(result['task_dir'])
            if task_dir.exists():
                # Verify required files
                self.assertTrue((task_dir / 'tb_meta.json').exists())
                self.assertTrue((task_dir / 'LM_INSTRUCTIONS.md').exists())
                self.assertTrue((task_dir / 'repo_info.json').exists())
                self.assertTrue((task_dir / 'diff.patch').exists())
                self.assertTrue((task_dir / 'notes.md').exists())
                
                # Clean up created task
                shutil.rmtree(task_dir)

class TestMCPServer(unittest.TestCase):
    """Test MCP Server"""
    
    def setUp(self):
        """Set up test environment"""
        self.server = MCPServer()
    
    def test_handle_initialize(self):
        """Test initialize request"""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {}
        }
        
        response = self.server.handle_request(request)
        
        self.assertEqual(response['jsonrpc'], "2.0")
        self.assertEqual(response['id'], 1)
        self.assertIn('result', response)
        self.assertIn('protocolVersion', response['result'])
        self.assertIn('capabilities', response['result'])
    
    def test_handle_list_tools(self):
        """Test list tools request"""
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        
        response = self.server.handle_request(request)
        
        self.assertEqual(response['jsonrpc'], "2.0")
        self.assertEqual(response['id'], 2)
        self.assertIn('result', response)
        self.assertIn('tools', response['result'])
        
        tools = response['result']['tools']
        self.assertIsInstance(tools, list)
        self.assertTrue(len(tools) > 0)
        
        # Check for expected tools
        tool_names = [t['name'] for t in tools]
        self.assertIn('repo.start_task.v1', tool_names)
        self.assertIn('repo.end_task.v1', tool_names)
        self.assertIn('repo.check_readiness.v1', tool_names)
        self.assertIn('repo.autofix_readiness.v1', tool_names)

class TestHTTPServer(unittest.TestCase):
    """Test HTTP Server functionality"""
    
    @classmethod
    def setUpClass(cls):
        """Start HTTP server for testing"""
        # Start server in subprocess
        cls.server_process = subprocess.Popen(
            [sys.executable, 'tool_server.py', '--port', '8888'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait for server to start
        time.sleep(2)
        
        cls.base_url = 'http://localhost:8888'
    
    @classmethod
    def tearDownClass(cls):
        """Stop HTTP server"""
        cls.server_process.terminate()
        cls.server_process.wait(timeout=5)
    
    def test_health_endpoint(self):
        """Test /health endpoint"""
        response = requests.get(f'{self.base_url}/health')
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertIn('status', data)
        self.assertEqual(data['status'], 'healthy')
    
    def test_check_readiness_endpoint(self):
        """Test /check-readiness endpoint"""
        response = requests.post(f'{self.base_url}/check-readiness', json={})
        self.assertIn(response.status_code, [200, 400])  # May fail if worktree not ready
        
        data = response.json()
        self.assertIn('ok', data)
        self.assertIn('issues', data)
        self.assertIn('summary', data)

class TestEndToEnd(unittest.TestCase):
    """End-to-end integration tests"""
    
    def test_complete_workflow(self):
        """Test complete task creation workflow"""
        manager = CITBTaskManager()
        
        try:
            # 1. Check readiness
            readiness = WorktreeReadiness.check_readiness()
            print(f"Readiness check: {readiness['summary']}")
            
            # 2. Start task
            start_result = manager.start_task(
                task_title="E2E Test Task",
                notes="End-to-end test of CITB workflow",
                labels=["test", "e2e"]
            )
            
            if not start_result['ok']:
                self.skipTest(f"Could not start task: {start_result}")
            
            print(f"Task started: {start_result['task_slug']}")
            
            # 3. Simulate some work (create a test file)
            test_file = Path('test_e2e_file.txt')
            test_file.write_text("Test content for E2E")
            
            # 4. End task
            with patch('mcp_citb_server.TraceExporter.export_session') as mock_export:
                mock_export.return_value = {
                    "session_id": "e2e_test_session",
                    "traces": [{"test": "trace"}],
                    "count": 1
                }
                
                end_result = manager.end_task(
                    summary="E2E test completed successfully",
                    labels=["completed"]
                )
            
            if not end_result['ok']:
                self.fail(f"Could not end task: {end_result}")
            
            print(f"Task ended: {end_result['task_dir']}")
            
            # 5. Verify outputs
            task_dir = Path(end_result['task_dir'])
            self.assertTrue(task_dir.exists())
            
            # Check all required files
            required_files = [
                'tb_meta.json',
                'LM_INSTRUCTIONS.md',
                'repo_info.json',
                'diff.patch',
                'notes.md',
                'trace/session_id.txt',
                'trace/session_clean.json',
                'evaluation/rubric_template.md',
                'evaluation/tests_skeleton/test_skeleton.py'
            ]
            
            for file_path in required_files:
                full_path = task_dir / file_path
                self.assertTrue(full_path.exists(), f"Missing: {file_path}")
            
            # Verify content of key files
            tb_meta = json.loads((task_dir / 'tb_meta.json').read_text())
            self.assertEqual(tb_meta['metadata']['title'], "E2E Test Task")
            
            repo_info = json.loads((task_dir / 'repo_info.json').read_text())
            self.assertIn('start_commit', repo_info)
            self.assertIn('end_commit', repo_info)
            
            print("E2E test completed successfully!")
            
        finally:
            # Clean up
            if test_file.exists():
                test_file.unlink()
            
            # Clean up task directory if created
            if 'end_result' in locals() and end_result['ok']:
                task_dir = Path(end_result['task_dir'])
                if task_dir.exists():
                    shutil.rmtree(task_dir)

def run_tests(verbose=False):
    """Run all tests"""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestGitHelpers))
    suite.addTests(loader.loadTestsFromTestCase(TestWorktreeReadiness))
    suite.addTests(loader.loadTestsFromTestCase(TestCITBTaskManager))
    suite.addTests(loader.loadTestsFromTestCase(TestMCPServer))
    
    # Skip HTTP tests if server can't start
    try:
        # Check if port 8888 is available
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('localhost', 8888))
        sock.close()
        
        if result != 0:  # Port is available
            suite.addTests(loader.loadTestsFromTestCase(TestHTTPServer))
    except:
        print("Skipping HTTP server tests")
    
    suite.addTests(loader.loadTestsFromTestCase(TestEndToEnd))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1)
    result = runner.run(suite)
    
    return result.wasSuccessful()

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Test CITB components')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    parser.add_argument('--quick', action='store_true', help='Run quick tests only')
    args = parser.parse_args()
    
    if args.quick:
        # Run only quick tests
        print("Running quick tests...")
        suite = unittest.TestSuite()
        suite.addTest(TestGitHelpers('test_get_current_branch'))
        suite.addTest(TestWorktreeReadiness('test_check_readiness'))
        suite.addTest(TestCITBTaskManager('test_generate_task_slug'))
        
        runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
        result = runner.run(suite)
        success = result.wasSuccessful()
    else:
        success = run_tests(args.verbose)
    
    sys.exit(0 if success else 1)