# Welcome to Syncwave

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

## Demo

**The most basic example:**

<video autoplay muted loop playsinline style="pointer-events: none; user-select: none; width: 100%;"><source src="assets/non_reactive.mp4" type="video/mp4"></video>

Changing a value in Python changes the JSON file, and vice versa. Data is validated on each side.

**Reactive store:**

<video autoplay muted loop playsinline style="pointer-events: none; user-select: none; width: 100%;"><source src="assets/reactive.mp4" type="video/mp4"></video>

The store now uses a reactive type. `numbers` is a live handle on the store: mutating it in place changes the file, and it gets updated when the file changes.

**Reactive model:**

<video autoplay muted loop playsinline style="pointer-events: none; user-select: none; width: 100%;"><source src="assets/model.mp4" type="video/mp4"></video>

With the `store` decorator, a reactive model can be defined and used in a store in one step. The store is reactive, and so is every instance.

## Documentation

Syncwave is designed to be _small_, _simple_, and _intuitive_.

The **[Usage](./usage/syncwave/)** guide is detailed and tries to address everything, but it shouldn't make you feel like the library is more complex than it is. The **[Cheat Sheet](./cheat_sheet/)** offers a quick overview, with what you actually need to know to _use_ it.

The **[API](./api/syncwave/)** surface is intentionally small. Everything works like normal objects (`dict`, `list`, Pydantic models, etc.), and if you're familiar with Python type hints you're good to go.

## Inspiration

If you've ever used [VSCode](https://code.visualstudio.com), you know you can change the settings either from the settings panel, or by editing a `settings.json` file. I always liked how the app is _two-way synced_ with the file: as soon as you change something from the panel, `settings.json` is updated, and the other way around, editing and saving the file updates the app's settings live.

If you don't know what I'm talking about, let's say you want to change the editor's theme. You can go to `Workbench > Appearance > Color Theme` in the settings and select your theme, or you can open `settings.json` and set `"workbench.colorTheme"` to the value you want. Regardless of where you make the change (panel or file), you see the other place get updated instantly, and the app takes on the new appearance. Very satisfying!

I needed something similar for a project I was working on, and was quite surprised to discover that there was no library to achieve this. Syncwave started as a way to fill that gap.

## The Missing Piece

[FastAPI](https://fastapi.tiangolo.com) + [Pydantic](https://pydantic.dev/docs/validation/latest/get-started/) make it extremely easy to build a web API. Consider the following example:

```python title="main.py"
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class Customer(BaseModel):
    name: str
    age: int


customers = [
    Customer(name="Alice", age=25),
    Customer(name="Bob", age=30),
]


@app.get("/customers")
def get_customers() -> list[Customer]:
    return customers


@app.post("/customers")
def create_customer(customer: Customer) -> Customer:
    customers.append(customer)
    return customer
```

Just like that, you now have a server with two API routes, data validation, and interactive documentation.

!!! success "Running this example"

    You'll need `"fastapi[standard]"` installed. Copy the code into `main.py`, run the command `fastapi dev`, and open <http://localhost:8000/docs> to interact with the API.

That's an actual working web server, _almost_ ready to be used in production. Obviously, data persistence is the missing piece: the initial list of customers is just hard-coded. As soon as you restart the server, your data is gone.

Now you need to set up a database (PostgreSQL, SQLite, Redis, MongoDB, etc.), install a Python client or an ORM to talk to it, manage connections and sessions, and write queries to read and write data...

That's a lot to add on top of what was otherwise a very simple setup.

Syncwave takes a radically different approach. Here's how you can update the example:

```python title="main.py" hl_lines="2 5 8 9 14"
from fastapi import FastAPI
from syncwave import SyncModel, Syncwave

app = FastAPI()
syncwave = Syncwave()


@syncwave.store(name="customers")
class Customer(SyncModel):
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

With these _five_ highlighted lines, you now have persistence. Notice how little changed: you still define your data with a model, you still work with a Python list (well, a reactive one), and your FastAPI routes are identical.

If you run that example, Syncwave creates a JSON file to back the data:

```json title="syncstores/customers.json"
[]
```

It starts as an empty JSON array. If the file already contains data, Syncwave loads it into `customers` at startup. Try it by adding this to the file:

```json title="syncstores/customers.json"
[
  {
    "name": "Alice",
    "age": 25
  },
  {
    "name": "Bob",
    "age": 30
  }
]
```

From here, the variable `customers` and the JSON file are two-way synced. Add a customer through the API, the file updates. Modify `customers` from anywhere in your Python code, same thing. It works the other way too: open `customers.json` in a text editor and make a change, or have another program write to it, and the in-memory list picks it up.

All changes are validated both ways. You can't add just anything to the list; it has to be a valid `Customer` as defined by your Pydantic model. This goes for edits to the JSON file too. If someone writes invalid data into it, Syncwave rejects the change and reverts the file to its last valid state.

One benefit I really wanted was transparency. With most databases, your data lives in a binary file managed by the database engine. You need a client or some tooling just to look at what's in there, and usually the server has to be running too. With Syncwave, each store is a JSON file. You can open it, read it, edit it at any time, whether the backend is running or not.

## Why Syncwave

### Abstraction

Usually, you need to think at least a little bit about how your data is saved: how and when to serialize it, how and when to read it, the database's type system and how it maps to your Python types, etc. Obviously an ORM solves many problems, but that's still an interface to control a database.

Syncwave offers an abstraction that is 100% Python: you interact purely with normal types and operations. You manipulate your data like you already do and _that's it_, everything always stays synchronized.

### Validation

Syncwave is only possible thanks to Pydantic.

It's what handles all validation (and serialization/deserialization as well). Your data is always valid, at all times, through all operations. It doesn't matter whether a change comes from an API route, an ad-hoc manipulation, a file you manually change, etc.

### Simplicity

The [example](#the-missing-piece) above shows just how easily you can create a server nowadays thanks to the amazing FastAPI: in under a minute, you have a working API with validation and documentation. But as soon as you want to persist data, you hit your first bump, and suddenly there are new things to install and learn, and glue code to write.

Of course, Syncwave _is not_ a replacement for a full-fledged database: there are obvious and inherent limitations, but for many projects Syncwave is a good starting point. You can always switch later when you outgrow it, and you won't even have to remove anything since you never wrote anything specific. You'll just have to write what you would have needed from day one without Syncwave.
