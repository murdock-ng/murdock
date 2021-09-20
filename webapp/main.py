from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles


routes = [
    Mount("/", app=StaticFiles(directory="webapp/build", html=True), name="app"),
]

app = Starlette(routes=routes)
