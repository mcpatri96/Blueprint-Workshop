# Build Blueprint Workshop

Use 64-bit Python with Tkinter on Windows. The release was built using Python 3.14.5 and PyInstaller 6.20.0.

~~~powershell
python -m pip install pyinstaller==6.20.0
python -m PyInstaller --clean --onedir --windowed --name Blueprint_Workshop blueprints.py
~~~

Keep the files in the generated application directory together. To run the source directly, use python blueprints.py.
