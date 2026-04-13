import re

with open('locale/de/LC_MESSAGES/django.po', 'r') as f:
    content = f.read()

replacements = {
    "Dienststatus und Latenz.": "Service-Status & Latenz.",
    "Administrative Einstellungen aufrufen": "Admin-Einstellungen öffnen",
    "Neue Daten von allen Plattformen laden": "Alle Daten aktualisieren",
    "Suche nach Task-ID, Titel oder Kunde...": "Suche ID, Titel, Kunde...",
    "Geben Sie einen Namen für Ihre aktuelle Filterkonfiguration ein.": "Namen für diese Filter eingeben.",
    "z.B. Meine hochpriorisierten Aufgaben": "z.B. Wichtige Aufgaben",
    "Klicken, um diese Filter zu speichern": "Filter speichern",
    "Gespeicherte Ansicht löschen": "Ansicht löschen",
    "Sind Sie sicher, dass Sie diese Ansicht löschen möchten? Dies kann nicht rückgängig gemacht werden.": "Ansicht unwiderruflich löschen?",
    "Nach Kundenname filtern": "Nach Kunde filtern",
    "Nach Abteilung oder Gruppe filtern": "Nach Gruppe filtern",
    "Nach zugewiesenem Benutzer im Quellsystem filtern": "Nach Benutzer filtern",
    "Nach aktuellem Aufgabenstatus filtern": "Nach Status filtern",
    "Nach Aufgabenpriorität filtern": "Nach Priorität filtern",
    "Erstellungsdatum": "Erstellt",
    "Zuletzt geändert": "Aktualisiert",
    "Fälligkeitsdatum": "Fällig",
    "Alle Aufgaben über alle Plattformen": "Gesamtaufgaben über alle",
    "Aufgaben, die dir zugewiesen sind.": "Dir zugewiesene Aufgaben",
    "Aufgaben, die sofortige Aufmerksamkeit erfordern": "Dringende Aufgaben",
    "Aufgaben, die Aufmerksamkeit erfordern": "Dringende Aufgaben",
    "Aufgaben, die auf Feedback warten": "Warten auf Feedback",
    "Alle Quellen ungefiltert": "Ungefiltert",
    "Benutzerdefinierter Filter:": "Eigener Filter:",
    "Aktuelle Filter als benutzerdefinierte Ansicht speichern": "Filter als Ansicht speichern",
    "Auf globale Ansicht ohne Filter zurücksetzen": "Alle Filter zurücksetzen",
    "Einrichtung erforderlich": "Einrichtung nötig",
    "ID in die Zwischenablage kopiert!": "ID kopiert!"
}

for old, new in replacements.items():
    content = content.replace(f'msgstr "{old}"', f'msgstr "{new}"')

# Some might be slightly different as the user tweaked them
# E.g. "Gesamtaufgaben über alle Plattformen"
content = content.replace('msgstr "Gesamtaufgaben über alle Plattformen"', 'msgstr "Aufgaben (Alle)"')

with open('locale/de/LC_MESSAGES/django.po', 'w') as f:
    f.write(content)
