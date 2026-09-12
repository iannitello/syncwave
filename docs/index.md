# Get Started

## Welcome to Syncwave

Turn plain JSON files into a live data store, two-way synced with your Python objects.

## Installation

Install from [PyPI](https://pypi.org/project/syncwave/).

=== "uv"

    ```bash
    uv add syncwave
    ```

=== "pip"

    ```bash
    pip install syncwave
    ```

Requires Python 3.10+.

## Motivation

### Initial Inspiration

If you've ever used [VSCode](https://code.visualstudio.com), you probably know you can change the settings either from the app's UI or by editing the `settings.json` file.

Something I always found nice is that VSCode is "two-way synced" with the file: As soon as you change something from the UI, `settings.json` gets updated, and conversely, editing and saving the file updates the live app's settings.

If you don't know what I'm talking about, let's say you want to change the editor's theme. You can open the settings panel and go to **Workbench > Appearance > Color Theme** and select your theme, or you can open `settings.json` and set `"workbench.colorTheme"` to the value you want. Regardless of where you make the change (panel or file), you see the other place get updated instantly, and the app takes on the new appearance. Very satisfying!

I needed something similar for a project I was working on, and was quite surprise to discover that there was no library to achieve this. Syncwave started as a way to fill in that gap.

### The Missing Piece

[FastAPI](https://fastapi.tiangolo.com) + [Pydantic](https://pydantic.dev/docs/validation/latest/get-started/) make it extremely easy to build a web API. Consider the following example:

```python title="main.py"
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class Customer(BaseModel):
    name: str
    age: int


customers = [
    Customer(name="John Doe", age=30),
    Customer(name="Jane Doe", age=25),
]


@app.get("/customers")
def get_customers() -> list[Customer]:
    return customers


@app.post("/customers")
def create_customer(customer: Customer) -> Customer:
    customers.append(customer)
    return customer
```

Boom! You now have a server with two API routes, data validation, and interactive documentation.

!!! success "Running this example"

    You'll need `"fastapi[standard]"` and `pydantic` installed. Copy the code into `main.py`, run the command `fastapi dev`, and open <http://localhost:8000/docs> to interact with the API.

This isn't just a toy example, that's an actual working web server, _almost_ ready to be used in production. But obviously, there's a _small_ missing piece: data persistence. The initial list of customers is just hard-coded. As soon as you restart the server your data is gone.

Now you need to set up a database (PostgreSQL, SQLite, Redis, MongoDB, etc.), install a Python client, or an ORM to talk to it, manage connections and sessions, and write queries to read and write data... That's a lot to add on top of what was otherwise a very simple setup.

Syncwave takes a radically different approach. Here's how we can update the example above to add persistence:

```python title="main.py" hl_lines="3 6 9 15"
from fastapi import FastAPI
from pydantic import BaseModel
from syncwave import Syncwave

app = FastAPI()
syncwave = Syncwave()


@syncwave.register(name="customers")
class Customer(BaseModel):
    name: str
    age: int


customers = syncwave["customers"]


@app.get("/customers")
def get_customers() -> list[Customer]:
    return customers


@app.post("/customers")
def create_customer(customer: Customer) -> Customer:
    customers.append(customer)
    return customer
```

With these _four_ highlighted lines, you now have persistence. Notice how little changed between the two examples. You still define your data with a Pydantic model, you still work with a normal Python list, and your FastAPI routes are identical.

If you run that example, Syncwave creates a JSON file to back the data:

```json title="syncstores/customers.json"
[]
```

It starts as an empty JSON array. But if the file already contained data (like the customers John and Jane Doe from the first example), Syncwave would load it into `customers` at startup.

```json title="syncstores/customers.json"
[
  {
    "name": "John Doe",
    "age": 30
  },
  {
    "name": "Jane Doe",
    "age": 25
  }
]
```

From here, the variable `customers` and the JSON file are two-way synced. Add a customer through the API, the file updates. Modify `customers` from anywhere in your Python code, same thing. It works the other way too: open `customers.json` in a text editor and make a change, or have another program write to it, and the in-memory list picks it up.

All changes are validated both ways. You can't add just anything to the list; it has to be a valid `Customer` as defined by your Pydantic model. This goes for edits to the JSON file too. If someone writes invalid data into it, Syncwave rejects the change and reverts the file to its last valid state.

One benefit I really wanted was transparency. With most databases, your data lives in a binary file managed by the database engine. You need a client or some tooling just to look at what's in there, and usually the server has to be running too. With Syncwave, each store is a JSON file. You can open it, read it, edit it at any time, whether the backend is running or not.

## Why Syncwave

<!-- You only think in Python. Normal types, Pydantic models, familiar collections. No serialization, no connections, no queries, no sync logic. Pydantic validates, JSON Schema defines the format, Syncwave handles the rest. -->

## Reactivity

<!-- The React/Vue parallel: they keep the DOM in sync with JS state, Syncwave keeps JSON files in sync with Python state. Link to the reactivity docs for the deep dive. -->

## Trade-offs

<!-- Honest about limitations. Not a real database. Full file rewrites. Memory-bound (but fast because of it, like Redis). No ACID/BASE guarantees. Real databases are sophisticated for good reasons. Syncwave is for when you want dead-simple persistence without the infrastructure. -->

<!-- It's never been this easy to quickly build applications thanks to powerful modern tools and standards. FastAPI gave perhaps the best example of what is now possible; By intelligently combining many great tools together (Uvicorn, Starlette, Pydantic, Python's type hints, JSON Schema, OpenAPI, Swagger), FastAPI offered a

Syncwave builds on JSON Schema and Pydantic to give you persistence with almost no effort. If you know Python types and how to define a Pydantic model, you already know everything you need.

!!! note

    Syncwave is a standalone library, not a FastAPI plugin. It works in any Python project (CLI tools, desktop apps, scripts, etc.).

    If you're not familiar with FastAPI, don't worry, there will be plenty of other examples. You can also check out the [First Steps](https://fastapi.tiangolo.com/tutorial/first-steps/) guide.

    That said, Syncwave especially shines with long-running processes like web servers, since the two-way sync stays active for as long as the program is running. -->
