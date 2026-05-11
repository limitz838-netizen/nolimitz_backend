from app.services.terminal_creator import TerminalCreator

creator = TerminalCreator()

result = creator.create_terminal("client_100")

print(result)