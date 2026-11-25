"""Windows interface for building independent weapon blueprints."""
from pathlib import Path
import shutil
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import os
import sys

from blueprints import (
    APP_NAME, DEFAULT_GAME, Game, MAX_SLOTS, RegistryImportRequired, check_name, load_registry, save_registry,
)
from game_discovery import installed_games, is_game_folder


def find_game(game_path, window, preference):
    if game_path is None:
        if preference.is_file():
            game_path = Path(preference.read_text(encoding='utf-8').strip())
        if not is_game_folder(game_path):
            found = installed_games()
            if len(found) == 1:
                return found[0]
    while not is_game_folder(game_path):
        selected = filedialog.askdirectory(parent=window, title='Select the Breakpoint folder containing GRB.exe',
                                           mustexist=True)
        if not selected:
            return None
        game_path = Path(selected)
    return game_path


def open_variant_list(path, game, window):
    try:
        return load_registry(path, game)
    except RegistryImportRequired as error:
        messagebox.showinfo('Import your saved variants',
                            f'{error}\n\nSelect variants.json from the previous tool\'s variants folder.',
                            parent=window)
    while True:
        chosen = filedialog.askopenfilename(parent=window, title='Open the previous variants.json',
                                            filetypes=[('Saved variant list', '*.json')])
        if not chosen:
            return None
        try:
            previous = Path(chosen)
            if not previous.is_file():
                raise ValueError('Select the saved variants.json file.')
            registry = load_registry(previous, game)
            if path.is_file():
                with path.open('rb') as source, tempfile.NamedTemporaryFile(
                        prefix='variants.previous-', suffix='.json', dir=path.parent, delete=False) as backup:
                    shutil.copyfileobj(source, backup)
            save_registry(path, registry)
            return registry
        except (OSError, ValueError, KeyError) as error:
            messagebox.showerror('Could not import variants', str(error), parent=window)


def matches_search(query, *fields):
    text = ' '.join(fields).casefold()
    return all(word in text for word in query.casefold().split())


def show(game_path):
    window = tk.Tk()
    window.title(APP_NAME)
    window.minsize(620, 440)
    window.option_add('*Font', ('Segoe UI', 10))
    window.withdraw()
    app_path = Path(sys.executable if getattr(sys, 'frozen', False) else __file__)
    folder = app_path.resolve().parent / 'variants'
    registry_path = folder / 'variants.json'
    preference = folder / 'game-folder.txt'
    try:
        game_path = find_game(game_path, window, preference)
        if game_path is None:
            window.destroy()
            return
        game = Game(game_path)
        registry = open_variant_list(registry_path, game, window)
        if registry is None:
            window.destroy()
            return
        if not registry_path.exists():
            save_registry(registry_path, registry)
        if game_path != DEFAULT_GAME or preference.is_file():
            preference.write_text(str(game_path.resolve()), encoding='utf-8')
    except (OSError, ValueError, KeyError) as error:
        messagebox.showerror('Cannot read Breakpoint', str(error), parent=window)
        window.destroy()
        return

    window.deiconify()
    content = ttk.Frame(window, padding=16)
    content.pack(fill='both', expand=True)
    content.columnconfigure(0, weight=1)
    content.rowconfigure(6, weight=1)
    ttk.Label(content, text='Duplicate a weapon', font=('Segoe UI', 14, 'bold')).grid(row=0, column=0, sticky='w')
    choices = {f'{weapon.label}  [{rid:X}]': rid for rid, weapon in game.weapons.items()}
    source_controls = ttk.Frame(content)
    source_controls.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(7, 0))
    ttk.Label(source_controls, text='Search weapons').pack(side='left')
    source_query = tk.StringVar()
    ttk.Entry(source_controls, textvariable=source_query).pack(side='left', fill='x', expand=True, padx=(8, 12))
    ttk.Label(source_controls, text='Sort').pack(side='left')
    source_order = tk.StringVar(value='A–Z')
    ttk.Combobox(source_controls, textvariable=source_order, values=('A–Z', 'Z–A'), state='readonly',
                 width=6).pack(side='left', padx=(8, 0))
    selection = tk.StringVar()
    weapon_list = ttk.Combobox(content, textvariable=selection, state='readonly')
    weapon_list.grid(row=2, column=0, sticky='ew', pady=(7, 4))
    source_count = tk.StringVar()
    ttk.Label(content, textvariable=source_count).grid(row=3, column=0, sticky='w')

    def refresh_sources(*_):
        labels = sorted((label for label in choices if matches_search(source_query.get(), label)),
                        key=str.casefold, reverse=source_order.get() == 'Z–A')
        weapon_list.configure(values=labels, state='readonly' if labels else 'disabled')
        if selection.get() not in labels:
            selection.set(labels[0] if len(labels) == 1 else '')
        source_count.set(f'{len(labels)} of {len(choices)} compatible weapons' if labels else 'No matching weapons.')

    source_query.trace_add('write', refresh_sources)
    source_order.trace_add('write', refresh_sources)
    refresh_sources()
    ttk.Label(content, text='Your variants', font=('Segoe UI', 14, 'bold')).grid(row=4, column=0, sticky='w', pady=(16, 5))
    search_controls = ttk.Frame(content)
    search_controls.grid(row=5, column=0, columnspan=2, sticky='ew', pady=(0, 8))
    ttk.Label(search_controls, text='Search variants').pack(side='left')
    variant_query = tk.StringVar()
    ttk.Entry(search_controls, textvariable=variant_query).pack(side='left', fill='x', expand=True, padx=(8, 12))
    variant_count = tk.StringVar()
    ttk.Label(search_controls, textvariable=variant_count).pack(side='right')
    table = ttk.Treeview(content, columns=('name', 'source'), show='headings', height=7, selectmode='browse')
    table.column('name', width=305, minwidth=180)
    table.column('source', width=185, minwidth=125)
    table.grid(row=6, column=0, columnspan=2, sticky='nsew')
    scroll = ttk.Scrollbar(content, orient='vertical', command=table.yview)
    scroll.grid(row=6, column=2, sticky='ns')
    table.configure(yscrollcommand=scroll.set)
    actions = ttk.Frame(content)
    actions.grid(row=7, column=0, columnspan=2, sticky='ew', pady=(8, 0))
    status = tk.StringVar(value='Add your variants, then click Build Variants.')
    ttk.Label(content, textvariable=status, wraplength=550).grid(row=8, column=0, columnspan=2, sticky='w', pady=(9, 0))
    headings = ('Blueprint name', 'Source weapon')
    sort_column, descending = 0, False

    def refresh(*_):
        selected_rows = table.selection()
        rows = []
        for row in registry['variants']:
            source = game.weapons.get(row['weapon'])
            fields = (row['name'], source.label if source else 'Unavailable')
            if matches_search(variant_query.get(), *fields):
                rows.append((row['slot'], fields))
        rows.sort(key=lambda item: (item[1][sort_column].casefold(), item[1][1-sort_column].casefold(), item[0]),
                  reverse=descending)
        table.delete(*table.get_children())
        for slot, fields in rows:
            table.insert('', 'end', iid=str(slot), values=fields)
        if selected_rows and table.exists(selected_rows[0]):
            table.selection_set(selected_rows[0])
            table.focus(selected_rows[0])
            table.see(selected_rows[0])
        variant_count.set(f'{len(rows)} of {len(registry["variants"])} variants')
        for i, name in enumerate(headings):
            direction = (' ▼' if descending else ' ▲') if i == sort_column else ''
            table.heading(i, text=name + direction, command=lambda column=i: sort_variants(column))

    def sort_variants(column):
        nonlocal sort_column, descending
        descending = not descending if sort_column == column else False
        sort_column = column
        refresh()

    variant_query.trace_add('write', refresh)

    def selected():
        picked = table.selection()
        if not picked:
            status.set('Select a variant in the list first.')
            return None
        return next(row for row in registry['variants'] if str(row['slot']) == picked[0])

    def apply(rows, next_slot):
        nonlocal registry
        updated = dict(registry, variants=rows, next_slot=next_slot, revision=registry['revision'] + 1)
        try:
            if load_registry(registry_path, game) != registry:
                raise ValueError('Another window changed the variant list. Close and reopen this tool.')
            save_registry(registry_path, updated)
        except (OSError, ValueError, KeyError) as error:
            messagebox.showerror('Could not save variants', str(error), parent=window)
            return
        registry = updated
        refresh()
        if rows:
            status.set('Changes saved. Click Build Variants when your list is ready.')
        else:
            status.set('All variants removed from the list. Uninstall the blueprint package in S0A with Breakpoint closed.')

    def build_variants():
        if not registry['variants']:
            status.set('There are no variants to build. To remove an installed package, close Breakpoint and uninstall it in S0A.')
            return
        output = folder / f"Blueprint_Workshop_0.2.{registry['revision']}.zip"
        status.set('Building the S0A package.')
        window.configure(cursor='wait')
        window.update_idletasks()
        try:
            active_game = Game(game_path)
            if load_registry(registry_path, active_game) != registry:
                raise ValueError('Another window changed the variant list. Close and reopen this tool.')
            active_game.build(registry, output)
        except (OSError, ValueError, KeyError) as error:
            status.set('Build failed. Your saved list is ready to retry.')
            messagebox.showerror('Could not build variants', str(error), parent=window)
        else:
            status.set(f'Built {output.name}. With Breakpoint closed, install it in S0A and relaunch.')
        finally:
            window.configure(cursor='')

    def duplicate():
        source = choices.get(selection.get())
        if source is None:
            status.set('Select a weapon from the filtered list first.')
            return
        slot = registry['next_slot']
        if slot >= MAX_SLOTS:
            status.set('This variant list reached its lifetime limit.')
            return
        name = simpledialog.askstring('Name the duplicate', 'Blueprint name', parent=window)
        if name is None:
            return
        try:
            name = check_name(name, registry)
        except ValueError as error:
            status.set(str(error))
            return
        apply([*registry['variants'], dict(slot=slot, weapon=source, name=name)], slot + 1)

    def rename_variant():
        row = selected()
        if row is None:
            return
        name = simpledialog.askstring('Rename variant', 'Blueprint name', initialvalue=row['name'], parent=window)
        if name is None:
            return
        try:
            name = check_name(name, registry, row['slot'])
        except ValueError as error:
            status.set(str(error))
            return
        if name != row['name']:
            apply([dict(r, name=name) if r['slot'] == row['slot'] else r for r in registry['variants']],
                  registry['next_slot'])

    def delete_variant():
        row = selected()
        if row is None:
            return
        if not messagebox.askokcancel('Delete variant',
                                      f"Remove {row['name']} from the next package?\n\n"
                                      'Equip a different weapon on any character using this variant before installing the update.',
                                      parent=window):
            return
        apply([r for r in registry['variants'] if r['slot'] != row['slot']], registry['next_slot'])

    ttk.Button(content, text='Duplicate…', command=duplicate).grid(row=2, column=1, padx=(8, 0))
    ttk.Button(actions, text='Rename…', command=rename_variant).pack(side='left')
    ttk.Button(actions, text='Delete…', command=delete_variant).pack(side='left', padx=8)
    ttk.Button(actions, text='Open package folder', command=lambda: os.startfile(folder)).pack(side='right')
    ttk.Button(actions, text='Build Variants', command=build_variants).pack(side='right', padx=(0, 8))
    refresh()
    window.mainloop()
