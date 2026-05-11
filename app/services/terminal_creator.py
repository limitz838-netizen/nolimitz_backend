import os
import shutil

BASE_TERMINAL_PATH = r"C:\Users\user\Desktop\NolimitzTerminals"
MASTER_TERMINAL = r"C:\Users\user\Desktop\NolimitzTerminals\client_1"


class TerminalCreator:

    def create_terminal(self, terminal_name):
        new_terminal_path = os.path.join(
            BASE_TERMINAL_PATH,
            terminal_name
        )

        if os.path.exists(new_terminal_path):
            return {
                "success": True,
                "message": "Terminal already exists"
            }

        shutil.copytree(
            MASTER_TERMINAL,
            new_terminal_path
        )

        return {
            "success": True,
            "terminal": terminal_name
        }