from app.services.terminal_manager import TerminalManager

manager = TerminalManager()

result = manager.login_terminal(
    terminal_name="client_1",
    login="161527062",
    password="Uthman7688@",
    server="ExnessKE-MT5Real21"
)

print(result)