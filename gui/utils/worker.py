#!/usr/bin/env python3
"""
Thread worker for running long tasks without blocking the GUI.
"""

import sys
import subprocess
from PyQt5.QtCore import QObject, pyqtSignal, QRunnable, pyqtSlot

class CommandWorker(QObject):
    """
    Worker class to execute shell commands and emit output line by line.
    """
    finished = pyqtSignal()
    output_line = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, command, cwd=None):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.process = None
        self.is_running = False

    def run(self):
        """Execute the command."""
        self.is_running = True
        try:
            # Prepare process
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=True,
                text=True,
                bufsize=1  # Line buffered
            )
            
            # Read output live
            for line in iter(self.process.stdout.readline, ''):
                if not self.is_running:
                    break
                self.output_line.emit(line.rstrip())
            
            self.process.stdout.close()
            return_code = self.process.wait()
            
            if return_code != 0:
                self.error_occurred.emit(f"Process exited with code {return_code}")
            
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            self.is_running = False
            self.finished.emit()

    def stop(self):
        """Stop the running process."""
        self.is_running = False
        if self.process:
            self.process.terminate()
