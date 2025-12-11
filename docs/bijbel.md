<chefchat_master_prompt_suite>
    <meta_instruction>
        Dit document bevat de volledige implementatie-roadmap voor 'ChefChat'.
        Voer de prompts uit in de aangegeven volgorde (Sequence 1 t/m 7).
        Elke prompt bouwt voort op het resultaat van de vorige.
    </meta_instruction>

    <prompt sequence="1" title="Core Architecture & Message Bus">
        <role>Senior Backend Python Engineer</role>
        <context>
            Je bouwt de fundering voor "ChefChat", een AI-Engineer gebaseerd op een keukenmetafoor.
            De architectuur is een **Asynchroon Actor Model** (Swarm) waarbij Agents communiceren via een centrale Message Bus.
        </context>
        <task>
            Zet de projectstructuur op en implementeer de communicatie-backbone.
        </task>
        <requirements>
            1. **Project Setup**:
               - Gebruik `poetry` voor dependency management (`textual`, `rich`, `pydantic`, `aiohttp`, `networkx`).
               - Mappenstructuur: `chefchat/kitchen`, `chefchat/interface`, `chefchat/pantry`.

            2. **Message Protocol (`chefchat/kitchen/message.py`)**:
               - Maak een Pydantic model `ChefMessage`.
               - Velden: `id` (uuid), `sender` (str), `recipient` (str), `type` (str), `payload` (dict), `priority` (int).

            3. **The Bus (`chefchat/kitchen/bus.py`)**:
               - Implementeer `KitchenBus` klasse met `asyncio.Queue`.
               - Functionaliteit: `subscribe(station_name)` en `publish(message)`.
               - Zorg voor thread-safe asynchrone afhandeling.

            4. **Base Agent (`chefchat/kitchen/base.py`)**:
               - Abstracte klasse `BaseStation`.
               - Moet een `listen()` loop hebben die berichten van de bus consumeert.
        </requirements>
        <output_format>
            Geef alleen de volledige Python-code voor de gevraagde bestanden en de terminal commando's. Geen markdown uitleg.
        </output_format>
    </prompt>

    <prompt sequence="2" title="TUI Implementation (Textual)">
        <role>Frontend Engineer (Textual/Rich Specialist)</role>
        <context>
            De backend staat. Nu bouwen we de Terminal User Interface (TUI) met de "Dark Kitchen Aesthetic".
            We gebruiken het `Textual` framework.
        </context>
        <task>
            Implementeer de 3-pane dashboard layout en de CSS styling.
        </task>
        <requirements>
            1. **Styling (`chefchat/interface/styles.tcss`)**:
               - Definieer variabelen voor het palet:
                 - Background: `#1a1b26` (Charcoal)
                 - Borders: `#414868` (Steel)
                 - Accent: `#e0af68` (Saffron Gold)
                 - Success: `#9ece6a`, Error: `#f7768e`.
               - Maak classes voor `.panel`, `.agent-active`, `.log-line`.

            2. **Widgets (`chefchat/interface/widgets.py`)**:
               - `TheTicket`: Bevat een `TextLog` (chat history) en `Input` (user command).
               - `ThePass`: Bevat statische labels voor Agents en `ProgressBar` widgets.
               - `ThePlate`: Een `Static` view voor code output (syntax highlighted).

            3. **Main App (`chefchat/interface/tui.py`)**:
               - Klasse `ChefChatApp(App)`.
               - Gebruik CSS Grid om de layout te maken:
                 - Boven: Ticket (Links) + Pass (Rechts).
                 - Onder: Plate (Volledige breedte).
        </requirements>
        <output_format>
            Geef de volledige inhoud van `styles.tcss` en `tui.py`.
        </output_format>
    </prompt>

    <prompt sequence="3" title="Agent Logic & Wiring">
        <role>Full Stack Python Architect</role>
        <context>
            We hebben de bus en de UI. Nu moeten we de Agents intelligentie geven en de UI aan de Bus koppelen zodat het scherm beweegt.
        </context>
        <task>
            Implementeer de `SousChef` en `LineCook` logica en verbindt de TUI met de async backend.
        </task>
        <requirements>
            1. **Agents (`chefchat/kitchen/stations/*.py`)**:
               - `SousChef`: Luistert naar 'NEW_TICKET'. Stuurt na 2 seconden (simulatie) een 'PLAN' naar de LineCook.
               - `LineCook`: Luistert naar 'PLAN'. Stuurt progress updates (0-100%) terug naar de bus terwijl hij werkt.

            2. **TUI Integratie (`chefchat/interface/tui.py` update)**:
               - Update de `ChefChatApp`.
               - Start de Agents (`start_shift`) als `textual workers` (`run_worker`).
               - Maak een `monitor_bus()` methode die asynchroon naar de bus luistert en de UI widgets (Logs, ProgressBars) update op basis van inkomende berichten.
               - Koppel de `Input` widget aan het verzenden van een 'NEW_TICKET' bericht naar de bus.
        </requirements>
        <output_format>
            Geef de code voor de agents en de *bijgewerkte* `tui.py` die alles aan elkaar knoopt.
        </output_format>
    </prompt>

    <prompt sequence="4" title="Knowledge Graph & AST Parsing">
        <role>Computer Science Specialist (Compilers & Graphs)</role>
        <context>
            De basis werkt. Nu voegen we "Phase 5: The Masterclass" toe.
            De Chef moet de code niet lezen als tekst, maar begrijpen als een structuur (AST).
        </context>
        <task>
            Implementeer de `IngredientsManager` die een Knowledge Graph opbouwt van de codebase.
        </task>
        <requirements>
            1. **Graph Engine (`chefchat/pantry/ingredients.py`)**:
               - Gebruik `networkx` om een graaf te bouwen.
               - Implementeer een scanner die recursief door de projectmap loopt.
               - Gebruik Python's `ast` module om klassen, functies en imports te extraheren.
               - Maak Nodes: `File`, `Class`, `Function`.
               - Maak Edges: `imports`, `defines`, `calls`.

            2. **Integration**:
               - Maak een commando `chef prep` beschikbaar in de TUI input.
               - Wanneer `chef prep` draait, moet de `IngredientsManager` de graaf bouwen en opslaan.
        </requirements>
        <output_format>
            Code voor `ingredients.py` en de update voor `sous_chef.py`.
        </output_format>
    </prompt>

    <prompt sequence="5" title="Recipe Engine (YAML Workflows)">
        <role>Systems Architect</role>
        <context>
            We implementeren "Phase 2: Recipes". We willen gestandaardiseerde workflows uitvoeren vanuit YAML bestanden.
        </context>
        <task>
            Bouw de `RecipeParser` en de logica om een recept stap-voor-stap uit te voeren.
        </task>
        <requirements>
            1. **Schema (`chefchat/pantry/recipes.py`)**:
               - Model voor YAML validatie.

            2. **Execution Engine**:
               - Update de `SousChef` om het commando `chef cook <recipe_name>` te begrijpen.
               - De SousChef moet het YAML bestand laden en stappen delegeren via de Bus.

            3. **Sample**:
               - Maak een voorbeeld YAML recept.
        </requirements>
        <output_format>
            Code voor `recipes.py` en de update in `SousChef`.
        </output_format>
    </prompt>

    <prompt sequence="6" title="Testing Sandbox & Auto-Correction">
        <role>QA & Security Engineer</role>
        <context>
            "Phase 4: The Taste Test". De code mag niet naar de gebruiker zonder tests.
        </context>
        <task>
            Implementeer de `Expeditor` (QA Agent) en de self-healing loop.
        </task>
        <requirements>
            1. **Agent (`chefchat/kitchen/stations/expeditor.py`)**:
               - Draait `pytest` of `ruff` (linter) in een subprocess.

            2. **Self-Healing Loop**:
               - Als de test faalt, vangt de Expeditor de foutmelding en stuurt deze terug naar de `LineCook` ("Fix this").
               - Maximaal 3 pogingen.

            3. **Integration**:
               - Voeg commando `chef taste` toe aan de TUI.
        </requirements>
        <output_format>
            Code voor `expeditor.py` en de integratie.
        </output_format>
    </prompt>

    <prompt sequence="7" title="The Secret Menu (Git & UX)">
        <role>Product Engineer</role>
        <context>
            De laatste loodjes: Git-integratie en UX animaties.
        </context>
        <task>
            Implementeer "Mise en Place" (Git snapshots) en visuele polish.
        </task>
        <requirements>
            1. **Mise en Place (Git)**:
               - Auto-stash of branch creatie voor een taak begint.
               - Commando: `chef undo`.

            2. **Visuals**:
               - ASCII "Whisk" spinner in logs.

            3. **The Critic**:
               - Commando `chef critic` voor sarcastische code reviews.
        </requirements>
        <output_format>
            Code snippets voor Git-integratie en TUI updates.
        </output_format>
    </prompt>

</chefchat_master_prompt_suite>
