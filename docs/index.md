# Get Started

## Welcome to Syncwave

Turn plain JSONs into a live data store, two-way synced with Python objects.

## Installation

Install from [PyPI](https://pypi.org/project/syncwave/).

=== "pip"

    ```bash
    pip install syncwave
    ```

=== "uv"

    ```bash
    uv add syncwave
    ```

Requires Python 3.9+.

## Motivation

### Initial Inspiration

If you've ever used [VSCode](https://code.visualstudio.com), you probably know you can change the settings either from the app's UI or by editing the `settings.json` file.

Something I always found nice is that VSCode is "two-way synced" with the file: As soon as you change something from the UI, `settings.json` gets updated, and conversely, editing and saving the file updates the app's settings.

Say you want to change the editor's theme. You can open the settings panel and go to **Workbench > Appearance > Color Theme** and select your theme, or you can open `settings.json` and set `"workbench.colorTheme"` to the value you want. Regardless of where you make the change (panel or file), you see the other place get updated instantly, and the app takes on the new appearance. Very satisfying!

I needed something similar for a Python project I was working on. I looked for a library to achieve this, but found nothing resembling what I wanted. Syncwave started as a way to fill in that gap.
