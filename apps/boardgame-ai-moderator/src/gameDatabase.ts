/**
 * Local board-game knowledge base.
 *
 * Provides instant search, game details, quick-start guides,
 * step-by-step tutorials and simulation data for 20+ popular games.
 * The backend API enriches / overrides this when available.
 */

// ── Core types ──────────────────────────────────────────────────────

export interface GameInfo {
  name: string;
  playerCount: string;
  complexity: 1 | 2 | 3;
  duration: string;
  description: string;
  quickStart: QuickStartData;
  steps: StepData[];
  simulationSteps: SimStep[];
}

export interface QuickStartData {
  goal: string;
  turns: string;
  winning: string;
}

export interface StepData {
  id: number;
  title: string;
  description: string;
  proTip?: string;
}

export interface SimStep {
  id: number;
  player: string;
  playerColor: string;
  narration: string;
  piecePosition: { x: number; y: number };
}

// ── Game catalogue ──────────────────────────────────────────────────

const GAMES: GameInfo[] = [
  {
    name: "Catan",
    playerCount: "3-4 players",
    complexity: 2,
    duration: "90 min",
    description:
      "In Catan, players compete to build the most prosperous settlement on an uncharted island. Collect and trade resources — brick, lumber, wool, grain, and ore — to build roads, settlements, and cities. Be the first to earn 10 victory points!",
    quickStart: {
      goal: "Be the first player to reach 10 victory points by building settlements (1 VP), cities (2 VP), and earning bonus cards.",
      turns: "Roll two dice → collect resources from adjacent hexes → trade with other players or the bank → build roads, settlements, or cities.",
      winning: "First player to reach 10 VP on their turn wins. Points come from settlements, cities, Longest Road (2 VP), Largest Army (2 VP), and Victory Point development cards.",
    },
    steps: [
      { id: 1, title: "Set up the board", description: "Place hexagonal terrain tiles randomly to form Catan island. Surround with ocean frame pieces. Place number tokens on each land hex.", proTip: "Spread similar resources apart for a more balanced game!" },
      { id: 2, title: "Place initial settlements", description: "Each player places 2 settlements and 2 roads. Go clockwise for the first, counter-clockwise for the second. Settlements must be at least 2 intersections apart.", proTip: "Prioritise hexes with 6 or 8 — they're rolled most often." },
      { id: 3, title: "Roll for resources", description: "On your turn, roll both dice. Every player with a settlement adjacent to a hex matching the sum collects that resource.", proTip: "A 7 activates the robber — anyone with 8+ cards must discard half." },
      { id: 4, title: "Trade & build", description: "Trade resources with other players (any ratio they'll accept) or with the bank at 4:1. Use resources to build roads (brick + lumber), settlements (brick + lumber + wool + grain), or cities (3 ore + 2 grain)." },
      { id: 5, title: "Development cards", description: "Buy a development card for ore + wool + grain. Knights let you move the robber. Some give free roads, resource monopoly, or hidden victory points.", proTip: "Having 3+ knights earns Largest Army (2 VP)." },
    ],
    simulationSteps: [
      { id: 1, player: "ALICE", playerColor: "text-player-red", narration: "Alice rolls a 6. Her settlement borders a lumber hex numbered 6 — she collects 1 lumber.", piecePosition: { x: 20, y: 20 } },
      { id: 2, player: "BOB", playerColor: "text-player-blue", narration: "Bob rolls an 8. His settlement borders a wheat hex numbered 8 — he collects 1 wheat.", piecePosition: { x: 60, y: 40 } },
      { id: 3, player: "ALICE", playerColor: "text-player-red", narration: "Alice trades 1 lumber + 1 brick with Bob for 1 ore, then builds a road towards the coast.", piecePosition: { x: 40, y: 60 } },
      { id: 4, player: "BOB", playerColor: "text-player-blue", narration: "Bob buys a dev card (ore + wool + grain) and draws a Knight. He plays it, moves the robber to Alice's brick hex, and steals 1 card.", piecePosition: { x: 30, y: 50 } },
    ],
  },
  {
    name: "Chess",
    playerCount: "2 players",
    complexity: 3,
    duration: "30-60 min",
    description:
      "Chess is a two-player strategy game where each side commands an army of 16 pieces. Manoeuvre your king, queen, rooks, bishops, knights, and pawns to checkmate your opponent's king — trapping it with no escape.",
    quickStart: {
      goal: "Checkmate your opponent's king — put it under attack with no legal move to escape.",
      turns: "White moves first. On each turn, move one piece following its movement rules. Capture opponent pieces by moving onto their square.",
      winning: "The game ends when a king is checkmated, a player resigns, or a draw condition is met (stalemate, insufficient material, threefold repetition, or 50-move rule).",
    },
    steps: [
      { id: 1, title: "Set up the board", description: "Place the board so each player has a white square on their bottom-right. Set pawns on the 2nd rank. Rooks in corners, then knights, then bishops. Queen goes on her colour, king beside her." },
      { id: 2, title: "Learn piece movement", description: "Pawns move forward 1 (or 2 from start), capture diagonally. Rooks move in straight lines. Bishops move diagonally. Queen combines both. Knights jump in an L-shape. King moves 1 square any direction.", proTip: "Knights are the only piece that can jump over others." },
      { id: 3, title: "Special moves", description: "Castling: move king 2 squares toward a rook, rook jumps over. En passant: capture a pawn that just moved 2 squares. Promotion: a pawn reaching the last rank becomes any piece (usually queen)." },
      { id: 4, title: "Check and checkmate", description: "Check: the king is under attack. The player must escape by moving, blocking, or capturing. Checkmate: no escape exists → game over.", proTip: "Always look for checks, captures, and threats — in that order." },
    ],
    simulationSteps: [
      { id: 1, player: "WHITE", playerColor: "text-player-red", narration: "White opens with e4 — the King's Pawn advances two squares, controlling the centre.", piecePosition: { x: 50, y: 40 } },
      { id: 2, player: "BLACK", playerColor: "text-player-blue", narration: "Black responds with e5, mirroring the central control.", piecePosition: { x: 50, y: 60 } },
      { id: 3, player: "WHITE", playerColor: "text-player-red", narration: "White develops the knight to f3, attacking the e5 pawn and eyeing the centre.", piecePosition: { x: 60, y: 30 } },
    ],
  },
  {
    name: "Monopoly",
    playerCount: "2-6 players",
    complexity: 1,
    duration: "120 min",
    description:
      "Monopoly is the classic property-trading board game. Buy streets, build houses and hotels, and collect rent from opponents. Drive your rivals into bankruptcy to become the last player standing!",
    quickStart: {
      goal: "Be the last player remaining with money. Bankrupt all opponents by owning property and charging rent.",
      turns: "Roll two dice → move your token → buy the property you land on (or auction it) → pay rent if owned by someone else → build houses/hotels on your colour groups.",
      winning: "When all other players are bankrupt, you win! In timed games, the richest player when time expires wins.",
    },
    steps: [
      { id: 1, title: "Set up", description: "Place the board, shuffle Chance and Community Chest cards. Each player picks a token and receives $1500 (2×$500, 2×$100, 2×$50, 6×$20, 5×$10, 5×$5, 5×$1). One player is the Banker." },
      { id: 2, title: "Rolling & moving", description: "Roll both dice and move that many spaces clockwise. If you roll doubles, take another turn (but 3 doubles in a row = jail).", proTip: "The most-landed-on properties are the orange group and railroads." },
      { id: 3, title: "Buying property", description: "Land on an unowned property? Buy it at the listed price, or it goes to auction. Collect the full colour group to start building houses." },
      { id: 4, title: "Building & rent", description: "With a complete colour group, buy houses ($50-$200 each) evenly across properties. 4 houses → upgrade to a hotel. Rent increases dramatically with each building.", proTip: "The housing shortage strategy: buy houses but don't upgrade to hotels to block others." },
    ],
    simulationSteps: [
      { id: 1, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 rolls a 7 and lands on Chance. The card says 'Advance to Go — collect $200'.", piecePosition: { x: 10, y: 50 } },
      { id: 2, player: "PLAYER 2", playerColor: "text-player-blue", narration: "Player 2 rolls a 5 and lands on Reading Railroad. They buy it for $200.", piecePosition: { x: 30, y: 20 } },
      { id: 3, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 rolls an 8 from Go and lands on Vermont Avenue. They purchase it for $100.", piecePosition: { x: 50, y: 20 } },
    ],
  },
  {
    name: "Wingspan",
    playerCount: "1-5 players",
    complexity: 2,
    duration: "60 min",
    description:
      "Wingspan is an engine-building board game about attracting birds to your wildlife preserves. Collect and play bird cards across three habitats — forest, grassland, and wetland — each producing unique resources.",
    quickStart: {
      goal: "Score the most points by collecting birds, caching food, laying eggs, tucking cards, and achieving end-of-round goals.",
      turns: "Choose one of four actions each turn: Play a bird, Gain food, Lay eggs, or Draw bird cards. Each action activates all birds in that habitat row.",
      winning: "After 4 rounds (with decreasing turns each round), total your birds' point values, bonus cards, end-of-round goals, cached food, tucked cards, and eggs.",
    },
    steps: [
      { id: 1, title: "Set up habitats", description: "Each player gets a player mat with three habitat rows (forest, grassland, wetland). Place the bird tray with 3 face-up cards. Fill the bird feeder dice tower with food dice." },
      { id: 2, title: "Play a bird", description: "Pay the bird's food cost (shown on the card) and an egg cost (for the 2nd+ bird in a row). Place the bird in its matching habitat. Each bird has a unique power.", proTip: "Birds with 'when activated' powers get stronger the earlier you play them." },
      { id: 3, title: "Gain food", description: "Take food dice from the bird feeder matching the food types your birds need. Activate each bird in your forest row from right to left." },
      { id: 4, title: "Lay eggs & draw cards", description: "Place eggs on your birds (limited by nest capacity). Or draw cards from the tray or deck. Activate grassland/wetland birds respectively." },
    ],
    simulationSteps: [
      { id: 1, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 plays a Red-tailed Hawk in the forest. It costs 1 rodent. Its power: when activated, hunt from the deck.", piecePosition: { x: 20, y: 30 } },
      { id: 2, player: "PLAYER 2", playerColor: "text-player-blue", narration: "Player 2 gains food — takes 1 seed and 1 fruit from the bird feeder, then activates their Spotted Towhee for a bonus seed.", piecePosition: { x: 40, y: 50 } },
      { id: 3, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 lays eggs on two birds in the grassland, then activates the Killdeer to draw an extra card.", piecePosition: { x: 60, y: 40 } },
    ],
  },
  {
    name: "Ticket to Ride",
    playerCount: "2-5 players",
    complexity: 1,
    duration: "45-60 min",
    description:
      "Ticket to Ride is a cross-country train adventure. Collect coloured train cards, claim railway routes on the map, and connect cities to complete destination tickets. The longest continuous route earns a bonus!",
    quickStart: {
      goal: "Score the most points by claiming routes, completing destination tickets, and building the longest continuous path.",
      turns: "On your turn, do ONE of three things: draw 2 train cards, claim a route by playing matching cards, or draw 3 destination tickets (keep at least 1).",
      winning: "When any player has 2 or fewer trains left, everyone (including them) gets one final turn. Total route points + completed tickets − incomplete tickets. Longest Route bonus = 10 VP.",
    },
    steps: [
      { id: 1, title: "Set up", description: "Place the board (map of routes). Each player gets 45 train pieces and a score marker at 0. Deal 4 train cards each and 3 destination tickets (keep at least 2)." },
      { id: 2, title: "Draw train cards", description: "Take 2 cards from the 5 face-up cards or the deck. Locomotives (wild) are face-up only — taking one counts as your full draw." , proTip: "Hoard cards of needed colours before claiming long routes for big points." },
      { id: 3, title: "Claim a route", description: "Play the required number of matching-colour cards to place your trains on a route. Longer routes score more: 1-car = 1pt, 2 = 2, 3 = 4, 4 = 7, 5 = 10, 6 = 15." },
      { id: 4, title: "Destination tickets", description: "Draw 3 new tickets, keep at least 1. Completed tickets earn bonus points. Incomplete tickets are negative points at game end!", proTip: "Pick tickets that share cities with routes you're already building." },
    ],
    simulationSteps: [
      { id: 1, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 draws 2 blue train cards from the deck, planning for the Seattle–Portland route.", piecePosition: { x: 20, y: 30 } },
      { id: 2, player: "PLAYER 2", playerColor: "text-player-blue", narration: "Player 2 claims the Nashville–Atlanta route (1 card) and scores 1 point.", piecePosition: { x: 60, y: 60 } },
      { id: 3, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 claims the 6-car Portland–San Francisco route for 15 points!", piecePosition: { x: 20, y: 50 } },
    ],
  },
  {
    name: "Azul",
    playerCount: "2-4 players",
    complexity: 2,
    duration: "30-45 min",
    description:
      "Azul challenges you to draft colourful tiles from shared factory displays and arrange them on your personal board to score points. Plan carefully — tiles you can't place cost penalty points!",
    quickStart: {
      goal: "Score the most points over multiple rounds by strategically placing tiles on your pattern lines and wall.",
      turns: "Pick all tiles of one colour from a factory display (leftovers go to centre). Place them on a pattern line. At round's end, filled lines move one tile onto your wall and score based on adjacency.",
      winning: "The game ends when any player completes a horizontal row on their wall. Final scoring adds bonuses for completed rows (+2), columns (+7), and full colour sets (+10).",
    },
    steps: [
      { id: 1, title: "Set up factories", description: "Place factory displays in the centre (5 for 2 players, up to 9 for 4). Draw 4 random tiles onto each factory from the bag." },
      { id: 2, title: "Drafting tiles", description: "Pick ALL tiles of one colour from one factory. Remaining tiles move to the centre. Or pick all of one colour from the centre (first to do so takes the -1 first-player marker)." , proTip: "Watch what your opponents need — deny them key colours!" },
      { id: 3, title: "Placing on pattern lines", description: "Add drafted tiles to one pattern line (rows 1-5). Each line holds only one colour. Excess tiles go to the floor line (penalty -1 to -3 each)." },
      { id: 4, title: "Wall tiling & scoring", description: "At round end, each completed pattern line slides one tile onto the wall. Score 1 point per tile, plus 1 for each adjacent tile in the same row/column." },
    ],
    simulationSteps: [
      { id: 1, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 drafts 3 blue tiles from a factory and places them on pattern line 3.", piecePosition: { x: 30, y: 20 } },
      { id: 2, player: "PLAYER 2", playerColor: "text-player-blue", narration: "Player 2 takes 2 red tiles from the centre, accepting the -1 first-player token.", piecePosition: { x: 50, y: 40 } },
      { id: 3, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 completes line 3 — one blue tile slides onto the wall scoring 3 points (adjacent to 2 existing tiles).", piecePosition: { x: 30, y: 40 } },
    ],
  },
  {
    name: "Codenames",
    playerCount: "4-8 players",
    complexity: 1,
    duration: "15-20 min",
    description:
      "Codenames is a party word game. Two teams race to identify their agents from a 5×5 grid of words. Spymasters give one-word clues linked to multiple words — but beware the assassin!",
    quickStart: {
      goal: "Your team must identify all your agents (words) from the grid before the other team. Avoid the assassin word (instant loss).",
      turns: "Spymaster gives a one-word clue + number (how many words it relates to). Team discusses and touches words one at a time. Correct = keep going. Wrong colour = end turn. Assassin = instant loss.",
      winning: "First team to identify all their agents wins. If a team touches the assassin, they lose immediately.",
    },
    steps: [
      { id: 1, title: "Set up", description: "Lay out 25 word cards in a 5×5 grid. Place the key card (visible only to Spymasters) showing which words belong to each team, which are neutral, and which is the assassin." },
      { id: 2, title: "Give a clue", description: "The Spymaster says ONE word and a number. The word must relate to the agents they want their team to guess. The number indicates how many agents the clue covers.", proTip: "Clues linking 2-3 words are the sweet spot — ambitious but safe." },
      { id: 3, title: "Guess", description: "The team discusses and touches a card. If correct (their colour), they may continue guessing up to clue number + 1. If neutral, turn ends. If other team's, it helps them and turn ends." },
      { id: 4, title: "Winning", description: "Identify all your agents to win. Be careful: touching the assassin word means your team immediately loses the game." },
    ],
    simulationSteps: [
      { id: 1, player: "RED SPYMASTER", playerColor: "text-player-red", narration: "Red Spymaster says 'OCEAN, 2' — hinting at 'WHALE' and 'SHIP' on the grid.", piecePosition: { x: 30, y: 30 } },
      { id: 2, player: "RED TEAM", playerColor: "text-player-red", narration: "Red team touches 'WHALE' — correct! They go for 'SHIP' — also correct! Two agents found.", piecePosition: { x: 50, y: 30 } },
      { id: 3, player: "BLUE SPYMASTER", playerColor: "text-player-blue", narration: "Blue Spymaster says 'NIGHT, 3' hinting at 'MOON', 'BAT', and 'STAR'.", piecePosition: { x: 30, y: 50 } },
    ],
  },
  {
    name: "Pandemic",
    playerCount: "2-4 players",
    complexity: 2,
    duration: "45 min",
    description:
      "Pandemic is a cooperative game where players work together as disease-fighting specialists to cure four deadly diseases spreading across the globe before time runs out.",
    quickStart: {
      goal: "Cure all four diseases before the outbreak counter reaches 8, the draw pile runs out, or any disease runs out of cubes.",
      turns: "Take 4 actions (move, treat, share knowledge, build station, cure). Draw 2 player cards (beware Epidemics!). Infect cities by drawing infection cards.",
      winning: "Cure all 4 diseases to win. You lose if 8 outbreaks occur, you can't place a disease cube, or the player deck runs out.",
    },
    steps: [
      { id: 1, title: "Set up", description: "Place the board. Set outbreak marker at 0, infection rate at 2. Shuffle and draw 9 infection cards: 3 cities get 3 cubes, 3 get 2, 3 get 1. Deal player cards (varies by count).", proTip: "Start on higher difficulty by adding more Epidemic cards." },
      { id: 2, title: "Take 4 actions", description: "Choose any 4: Drive (move to adjacent city), Direct Flight (discard card to fly there), Charter Flight (discard current city card to fly anywhere), Shuttle (between research stations), Treat Disease, Build Station, Share Knowledge, Discover Cure." },
      { id: 3, title: "Draw & infect", description: "Draw 2 player cards. If an Epidemic appears: increase infection rate, draw bottom infection card and add 3 cubes, shuffle infection discard and put on top. Then draw infection cards equal to the rate." },
      { id: 4, title: "Cure a disease", description: "At a research station, discard 5 cards of one colour to cure that disease. If all cubes of a cured disease are removed, it's eradicated (no new cubes placed).", proTip: "The Medic can remove all cubes of one colour as a single action." },
    ],
    simulationSteps: [
      { id: 1, player: "MEDIC", playerColor: "text-player-red", narration: "The Medic moves to Tokyo and treats the red disease, removing all 3 cubes in one action.", piecePosition: { x: 70, y: 30 } },
      { id: 2, player: "RESEARCHER", playerColor: "text-player-blue", narration: "The Researcher shares a blue city card with the Scientist, then flies to Paris to treat 1 blue cube.", piecePosition: { x: 40, y: 40 } },
      { id: 3, player: "SCIENTIST", playerColor: "text-player-red", narration: "The Scientist only needs 4 cards to cure! They discard 4 blue cards at the research station — Blue disease CURED!", piecePosition: { x: 30, y: 50 } },
    ],
  },
  {
    name: "7 Wonders",
    playerCount: "3-7 players",
    complexity: 2,
    duration: "30 min",
    description:
      "7 Wonders is a card-drafting civilisation game. Over three ages, draft cards from hands passed around the table to build your ancient city, advance science, raise armies, and construct your Wonder.",
    quickStart: {
      goal: "Score the most points across military, science, commerce, guilds, civic structures, and your Wonder.",
      turns: "Each turn: choose 1 card from your hand, play it simultaneously with all players, then pass remaining cards to your neighbour. Three ways to play a card: build it (pay its cost), use it to build a Wonder stage, or discard it for 3 coins.",
      winning: "After 3 Ages (each with 6 turns), tally military, coins, Wonder stages, civic (blue), commerce (yellow), guilds (purple), and science. Highest total wins.",
    },
    steps: [
      { id: 1, title: "Set up", description: "Each player gets a Wonder board (random or drafted). Separate Age I, II, III card decks. Deal 7 Age I cards to each player. Each player starts with 3 coins." },
      { id: 2, title: "Card drafting", description: "Choose 1 card, place it face-down. All reveal simultaneously. Pay costs (resources from your buildings or buy from neighbours for 2 coins). Pass remaining cards left (Age I/III) or right (Age II).", proTip: "Hate-draft: take a card an opponent desperately needs, even if you discard it." },
      { id: 3, title: "Build your Wonder", description: "Instead of building a card, tuck it face-down under your Wonder to build the next stage. Each stage gives unique bonuses (points, resources, free builds, military)." },
      { id: 4, title: "Military & scoring", description: "At each Age end, compare military strength with neighbours. Win = +1/3/5 tokens, Lose = -1 token. Science scores exponentially: collect sets of 3 different symbols for 7 points each, plus square of identical symbols." },
    ],
    simulationSteps: [
      { id: 1, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 builds a Lumber Yard (free) — gaining 1 wood production for future builds.", piecePosition: { x: 20, y: 30 } },
      { id: 2, player: "PLAYER 2", playerColor: "text-player-blue", narration: "Player 2 builds their first Wonder stage using a discarded card, gaining a free shield.", piecePosition: { x: 50, y: 30 } },
      { id: 3, player: "PLAYER 1", playerColor: "text-player-red", narration: "Age I ends — military comparison: Player 1 wins vs left neighbour (+1) but loses to right (-1).", piecePosition: { x: 35, y: 50 } },
    ],
  },
  {
    name: "Splendor",
    playerCount: "2-4 players",
    complexity: 1,
    duration: "30 min",
    description:
      "Splendor is a gem-trading engine-builder. Collect gem tokens, purchase development cards that give permanent gem bonuses, and attract nobles. First to 15 prestige points wins!",
    quickStart: {
      goal: "Reach 15 prestige points first by buying development cards and attracting noble tiles.",
      turns: "On your turn, do ONE thing: take 3 different gem tokens, take 2 same-colour tokens (if 4+ available), reserve a card (get 1 gold wild), or buy a development card.",
      winning: "When a player reaches 15+ points at the end of a round, the player with the most points wins.",
    },
    steps: [
      { id: 1, title: "Set up", description: "Lay out 4 cards from each of 3 tiers. Place gem tokens (varies by player count). Reveal nobles equal to players + 1." },
      { id: 2, title: "Collect gems", description: "Take 3 different gems or 2 of the same colour (only if 4+ of that colour remain). Max 10 tokens in hand.", proTip: "Early game: collect gems. Mid game: buy cheap cards for permanent bonuses. Late game: go for high-point cards." },
      { id: 3, title: "Buy cards", description: "Pay the gem cost shown on a card (your owned cards count as permanent discounts). The card gives you a permanent gem bonus and possibly prestige points." },
      { id: 4, title: "Nobles", description: "If you meet a noble's requirements (specific card bonuses, e.g. 4 blue + 4 green), they visit you automatically for 3 prestige points." },
    ],
    simulationSteps: [
      { id: 1, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 takes 3 gems: 1 diamond, 1 sapphire, 1 emerald.", piecePosition: { x: 30, y: 30 } },
      { id: 2, player: "PLAYER 2", playerColor: "text-player-blue", narration: "Player 2 buys a Tier 1 card for 3 sapphires — gaining a permanent ruby bonus.", piecePosition: { x: 50, y: 40 } },
      { id: 3, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 reserves a Tier 3 card (5 points!) and takes 1 gold wild token.", piecePosition: { x: 30, y: 50 } },
    ],
  },
  {
    name: "Scrabble",
    playerCount: "2-4 players",
    complexity: 2,
    duration: "60-90 min",
    description:
      "Scrabble is the classic word-building game. Place letter tiles on a 15×15 board to form words crossword-style. Premium squares multiply individual letter or whole word scores.",
    quickStart: {
      goal: "Score the most points by forming high-scoring words on the board using your letter tiles.",
      turns: "Draw 7 tiles. On your turn: place a word on the board (it must connect to existing tiles, crossword-style), score it, then draw replacement tiles. Or swap tiles/pass.",
      winning: "The game ends when the tile bag is empty and one player uses all their tiles (they get bonus points equal to opponents' remaining tiles). Highest score wins.",
    },
    steps: [
      { id: 1, title: "Set up", description: "Place all 100 tiles in the bag. Each player draws 7 tiles to their rack (hidden from others). The first word must cross the centre star." },
      { id: 2, title: "Form words", description: "Play tiles to form a word that connects to the existing board. Every new word and cross-word formed must be valid. Blank tiles are wild (0 points).", proTip: "Q, Z, X, J are worth 10, 10, 8, 8 points respectively — learn 2-letter words using them!" },
      { id: 3, title: "Premium squares", description: "DL = Double Letter, TL = Triple Letter, DW = Double Word, TW = Triple Word. Bonuses apply only when first covered. A word spanning two DW squares is quadrupled!" },
      { id: 4, title: "Bingo bonus", description: "Using all 7 tiles in one turn (a 'bingo') earns a 50-point bonus on top of the word score.", proTip: "Keep a balanced rack: mix of vowels and common consonants (R, S, T, L, N, E)." },
    ],
    simulationSteps: [
      { id: 1, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 opens with 'QUEST' on the centre star — Q on a double letter square — scoring 48 points!", piecePosition: { x: 50, y: 50 } },
      { id: 2, player: "PLAYER 2", playerColor: "text-player-blue", narration: "Player 2 extends 'QUEST' by adding 'ION' to make 'QUESTION' — double word square — 72 points!", piecePosition: { x: 60, y: 50 } },
    ],
  },
  {
    name: "Uno",
    playerCount: "2-10 players",
    complexity: 1,
    duration: "20-30 min",
    description:
      "UNO is the classic colour-matching card game. Match cards by colour or number, use action cards to disrupt opponents, and be the first to empty your hand. Don't forget to shout 'UNO!' with one card left!",
    quickStart: {
      goal: "Be the first to play all your cards. When you're down to one card, shout 'UNO!' — or draw 2 penalty cards if caught.",
      turns: "Play a card matching the top discard by colour, number, or symbol. No match? Draw 1 card (play it if valid). Action cards: Skip, Reverse, Draw Two, Wild, Wild Draw Four.",
      winning: "First player to empty their hand scores points from opponents' remaining cards. First to 500 cumulative points wins the game.",
    },
    steps: [
      { id: 1, title: "Deal cards", description: "Shuffle the 108-card deck. Deal 7 cards to each player. Place remaining cards face-down as the draw pile. Flip the top card to start the discard pile." },
      { id: 2, title: "Play or draw", description: "Match the discard's colour, number, or symbol. No match? Draw 1 card from the pile. Wild cards can be played on anything — you choose the new colour.", proTip: "Save Wild and Draw Four cards for critical moments." },
      { id: 3, title: "Action cards", description: "Skip: next player loses their turn. Reverse: changes play direction. Draw Two: next player draws 2 and loses their turn. Wild Draw Four: choose colour + next player draws 4 (can be challenged!)." },
      { id: 4, title: "Say UNO!", description: "When you play your second-to-last card, shout 'UNO!' If another player catches you forgetting, you must draw 2 penalty cards." },
    ],
    simulationSteps: [
      { id: 1, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 plays a Blue 7 on top of a Blue 3.", piecePosition: { x: 40, y: 40 } },
      { id: 2, player: "PLAYER 2", playerColor: "text-player-blue", narration: "Player 2 plays a Red 7 (matching number), changing the colour to red.", piecePosition: { x: 50, y: 40 } },
      { id: 3, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 plays Wild Draw Four! Changes colour to green. Player 2 draws 4 cards!", piecePosition: { x: 40, y: 50 } },
    ],
  },
  {
    name: "Risk",
    playerCount: "2-6 players",
    complexity: 2,
    duration: "120-180 min",
    description:
      "Risk is the classic game of global domination. Deploy armies, attack enemy territories, and forge alliances. Control continents for bonus reinforcements and eliminate all opponents to conquer the world.",
    quickStart: {
      goal: "Conquer the world by eliminating all opponents or complete your secret mission (mission variant).",
      turns: "Get reinforcements (territories ÷ 3 + continent bonuses + card sets) → Attack adjacent territories with dice → Fortify by moving armies between connected territories.",
      winning: "Standard: last player standing. Mission: complete your secret mission card. Capital: capture all opponents' capitals.",
    },
    steps: [
      { id: 1, title: "Set up", description: "Deal territory cards to divide the map. Place 1 army on each territory. Players take turns placing remaining armies on their territories. Remove dealt territory cards." },
      { id: 2, title: "Reinforcement", description: "At the start of your turn, receive armies: (territories ÷ 3, minimum 3) + continent bonuses + matching card set trade-ins. Place them on your territories.", proTip: "Hold continents with few border territories — Australia (2 borders) and South America (2 borders) are easiest." },
      { id: 3, title: "Attack", description: "Attack adjacent enemy territory using 1-3 dice (must have more armies than dice). Defender rolls 1-2 dice. Compare highest dice, then second-highest. Attacker wins ties? No — defender wins ties." },
      { id: 4, title: "Fortify", description: "Move any number of armies from one territory to an adjacent connected territory (once per turn). End your turn. If you conquered at least 1 territory this turn, draw a Risk card." },
    ],
    simulationSteps: [
      { id: 1, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 places 5 reinforcement armies on Ukraine, preparing a European campaign.", piecePosition: { x: 55, y: 25 } },
      { id: 2, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 attacks Scandinavia from Ukraine: rolls 6,5,3 vs defender's 4,2 — wins both! Scandinavia captured.", piecePosition: { x: 50, y: 15 } },
      { id: 3, player: "PLAYER 2", playerColor: "text-player-blue", narration: "Player 2 fortifies North Africa by moving 3 armies from Brazil, strengthening the African front.", piecePosition: { x: 40, y: 55 } },
    ],
  },
  {
    name: "Clue",
    playerCount: "3-6 players",
    complexity: 1,
    duration: "45 min",
    description:
      "Clue (Cluedo) is a deduction mystery game. A murder has occurred — who did it, with what weapon, in which room? Move through the mansion, make suggestions, and use logic to solve the crime.",
    quickStart: {
      goal: "Be the first to correctly deduce the murderer, weapon, and room hidden in the confidential envelope.",
      turns: "Roll and move → enter a room → make a suggestion (person + weapon + this room) → players disprove by privately showing you a matching card → make notes.",
      winning: "When confident, make an Accusation (any turn, from anywhere). Check the envelope. Correct = you win! Wrong = you're eliminated but still disprove others' suggestions.",
    },
    steps: [
      { id: 1, title: "Set up", description: "Separate suspect, weapon, and room cards. Randomly place 1 of each in the confidential envelope (the solution). Shuffle remaining cards and deal evenly to players." },
      { id: 2, title: "Move & suggest", description: "Roll dice and move. Enter a room to make a suggestion: 'I suggest it was [person] with the [weapon] in the [room you're in].' The named suspect token is moved to the room.", proTip: "Use secret passages to move quickly between opposite corners of the mansion." },
      { id: 3, title: "Disproving", description: "Going clockwise, the first player who holds any suggested card must privately show you ONE matching card. If no one can disprove, the suggestion might be correct!" },
      { id: 4, title: "Make your accusation", description: "When you think you know the answer, make an accusation on your turn: name the suspect, weapon, and room. Check the envelope secretly. Right = WIN! Wrong = you're out but still help disprove." },
    ],
    simulationSteps: [
      { id: 1, player: "MISS SCARLET", playerColor: "text-player-red", narration: "Miss Scarlet enters the Library and suggests: 'Colonel Mustard with the candlestick in the Library.'", piecePosition: { x: 30, y: 30 } },
      { id: 2, player: "MR. GREEN", playerColor: "text-[#4CAF50]", narration: "Mr. Green privately shows Miss Scarlet the Library card — eliminating the Library as the crime scene.", piecePosition: { x: 50, y: 40 } },
      { id: 3, player: "MISS SCARLET", playerColor: "text-player-red", narration: "Miss Scarlet crosses off Library on her notepad and heads for the Kitchen through the secret passage.", piecePosition: { x: 70, y: 70 } },
    ],
  },
  {
    name: "Terraforming Mars",
    playerCount: "1-5 players",
    complexity: 3,
    duration: "120 min",
    description:
      "Terraforming Mars puts you in charge of a corporation making Mars habitable. Raise the temperature, add oceans, and increase oxygen by playing project cards. The corporation contributing most to terraforming wins.",
    quickStart: {
      goal: "Earn the most Terraform Rating (TR) and victory points by contributing to Mars's transformation: raising temperature, oxygen, and placing ocean tiles.",
      turns: "Each generation: draw 4 cards (buy any for 3 MC each) → take 1-2 actions per turn (play cards, use standard projects, claim milestones, fund awards, convert plants/heat) → repeat until both players pass.",
      winning: "Game ends when all 3 global parameters are maxed (temperature 8°C, oxygen 14%, 9 oceans). Final score = TR + VP from cards, board tiles, awards, and milestones.",
    },
    steps: [
      { id: 1, title: "Set up", description: "Each player picks a corporation (gives starting MC and special ability). Place the board with Mars. Set global parameters to starting positions. Shuffle project deck." },
      { id: 2, title: "Research phase", description: "Draw 4 project cards. Keep any by paying 3 MC each. Discard the rest. This is your hand for future actions.", proTip: "Focus on cards that synergise — plant-based cards, space cards, or microbe cards work well together." },
      { id: 3, title: "Action phase", description: "Take turns performing 1-2 actions: play a project card, use a standard project, convert 8 plants → greenery tile (+1 oxygen, +1 TR), convert 8 heat → raise temperature (+1 TR), or claim milestones/fund awards." },
      { id: 4, title: "Production", description: "After all players pass, production phase: gain MC, steel, titanium, plants, energy, and heat based on your production tracks. Energy converts to heat." },
    ],
    simulationSteps: [
      { id: 1, player: "THARSIS REPUBLIC", playerColor: "text-player-red", narration: "Tharsis Republic plays 'Asteroid Mining' (cost 30 MC) gaining +2 titanium production.", piecePosition: { x: 40, y: 30 } },
      { id: 2, player: "ECOLINE", playerColor: "text-player-blue", narration: "Ecoline converts 7 plants (special ability: only 7 needed) into a greenery tile — oxygen rises to 3%, TR goes up!", piecePosition: { x: 50, y: 50 } },
      { id: 3, player: "THARSIS REPUBLIC", playerColor: "text-player-red", narration: "Tharsis Republic places an ocean tile, receiving 2 MC placement bonus and +1 TR.", piecePosition: { x: 35, y: 40 } },
    ],
  },
  {
    name: "Dominion",
    playerCount: "2-4 players",
    complexity: 2,
    duration: "30 min",
    description:
      "Dominion is the original deck-building game. Start with a small deck of copper and estates, then buy action and treasure cards to build an engine. Green victory point cards clog your deck — time your purchases!",
    quickStart: {
      goal: "Have the most victory points in your deck when the game ends (when Province pile or any 3 supply piles are empty).",
      turns: "Action phase: play 1 action card → Buy phase: play treasure cards, buy 1 card from supply → Clean-up: discard hand and played cards, draw 5 new cards.",
      winning: "Game ends when Provinces run out or 3 supply piles are empty. Count VP in your deck. Estates = 1, Duchies = 3, Provinces = 6.",
    },
    steps: [
      { id: 1, title: "Set up", description: "Each player starts with 7 Coppers and 3 Estates. Shuffle and draw 5. Set out treasure/VP supply stacks. Choose 10 Kingdom cards for the supply." },
      { id: 2, title: "Action phase", description: "Play 1 action card (unless a card gives you +Actions). Follow the card's instructions: +Cards, +Actions, +Buys, +Money, or special effects.", proTip: "Terminal actions (no +Actions) are risky — too many and you'll draw dead hands." },
      { id: 3, title: "Buy phase", description: "Play treasures from your hand. Buy 1 card costing ≤ your total money. The card goes to your discard pile (you'll draw it later when you reshuffle)." },
      { id: 4, title: "Clean-up", description: "Put everything (hand + played cards) in your discard. Draw 5 new cards. If your deck runs out, shuffle your discard pile to form a new deck." },
    ],
    simulationSteps: [
      { id: 1, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 plays a Village (+1 Card, +2 Actions), then a Smithy (+3 Cards), drawing 4 total cards!", piecePosition: { x: 30, y: 30 } },
      { id: 2, player: "PLAYER 2", playerColor: "text-player-blue", narration: "Player 2 plays 3 Coppers and 1 Silver (total 5 coins) to buy a Gold card.", piecePosition: { x: 50, y: 40 } },
      { id: 3, player: "PLAYER 1", playerColor: "text-player-red", narration: "Player 1 has 8 coins — buys a Province (6 VP)! The Province pile shrinks to 7.", piecePosition: { x: 30, y: 50 } },
    ],
  },
  {
    name: "Dixit",
    playerCount: "3-8 players",
    complexity: 1,
    duration: "30 min",
    description:
      "Dixit is a creative storytelling card game with dreamlike illustrations. The storyteller gives a clue about their card, everyone submits a matching card, then all vote. Be clever — too obvious or too vague and you score nothing!",
    quickStart: {
      goal: "Score 30 points first. The storyteller earns points when SOME (but not all) players guess their card correctly.",
      turns: "Storyteller gives a clue (word/phrase/sound) → all others submit a card that matches → cards are shuffled and revealed → everyone votes → scoring based on guesses.",
      winning: "If ALL or NONE guess correctly, storyteller scores 0 and everyone else scores 2. Otherwise, storyteller and correct guessers score 3. Non-storytellers also score 1 per vote on their decoy card.",
    },
    steps: [
      { id: 1, title: "Deal cards", description: "Each player draws 6 large illustrated cards. The storyteller (rotates each round) looks at their hand and comes up with a clue." },
      { id: 2, title: "Submit cards", description: "The storyteller says their clue aloud. Every other player chooses 1 card from their hand that best matches the clue and submits it face-down.", proTip: "As storyteller, aim for a clue that 1-2 players will get — not everyone." },
      { id: 3, title: "Vote", description: "Shuffle all submitted cards (including storyteller's) and reveal them. Each non-storyteller votes for which card they think is the storyteller's. You cannot vote for your own card." },
      { id: 4, title: "Scoring", description: "Storyteller scores 3 if some (not all) guess right. Correct guessers score 3. Each non-storyteller also scores 1 per vote their decoy received." },
    ],
    simulationSteps: [
      { id: 1, player: "STORYTELLER", playerColor: "text-player-red", narration: "The storyteller says 'A dream you can't quite remember' and places a card face-down.", piecePosition: { x: 40, y: 30 } },
      { id: 2, player: "ALL PLAYERS", playerColor: "text-player-blue", narration: "Each player picks their most dream-like card and submits it face-down. 5 cards are shuffled together.", piecePosition: { x: 40, y: 50 } },
      { id: 3, player: "VOTING", playerColor: "text-player-red", narration: "Cards are revealed. 2 of 4 players guess correctly — storyteller and correct guessers each score 3 points!", piecePosition: { x: 40, y: 60 } },
    ],
  },
  {
    name: "Gloomhaven",
    playerCount: "1-4 players",
    complexity: 3,
    duration: "90-150 min",
    description:
      "Gloomhaven is a tactical combat adventure. Play as mercenaries exploring dungeons, fighting monsters, and making story choices. Each scenario uses card-driven combat where every decision matters.",
    quickStart: {
      goal: "Complete scenarios by defeating enemies and achieving objectives. Level up your character, unlock new classes, and advance the branching campaign story.",
      turns: "Each round: secretly select 2 ability cards → reveal initiative (top card's number) → act in initiative order playing top of one card + bottom of the other → monsters act via AI cards → manage your shrinking hand.",
      winning: "Complete the scenario objective (usually defeat all enemies in the final room). If your hand/discard runs empty, you're exhausted and eliminated from the scenario.",
    },
    steps: [
      { id: 1, title: "Scenario set up", description: "Read the scenario book. Lay out map tiles, place monsters and obstacles. Each player picks 2 ability cards from their hand (varies by class, usually 9-12 cards)." },
      { id: 2, title: "Card selection", description: "Secretly pick 2 cards. The initiative number on your leading card determines turn order. Lower = faster. Reveal simultaneously with all players and monsters.", proTip: "Going early is great for killing enemies before they act. Going late lets you see what happens first." },
      { id: 3, title: "Actions", description: "Play the top half of one card and the bottom half of the other (or vice versa). Top halves are usually attacks, bottom halves are usually moves. Some cards have powerful one-time 'Loss' abilities." },
      { id: 4, title: "Rest & recover", description: "When you run out of cards in hand, perform a long rest: take a full round off, recover your discard pile (minus 1 card permanently). Your hand shrinks each cycle — manage it carefully!" },
    ],
    simulationSteps: [
      { id: 1, player: "BRUTE", playerColor: "text-player-red", narration: "The Brute leads initiative 12, charges into the room, and plays 'Trample' — attacking 2 adjacent enemies for 3 damage each.", piecePosition: { x: 40, y: 30 } },
      { id: 2, player: "SPELLWEAVER", playerColor: "text-player-blue", narration: "Spellweaver acts on initiative 21, casting 'Fire Orbs' — a Loss card dealing 3 damage to all enemies in a 3-hex area.", piecePosition: { x: 30, y: 50 } },
      { id: 3, player: "MONSTERS", playerColor: "text-[#888]", narration: "The remaining Living Bones (initiative 45) move toward the nearest player and attack the Brute for 2 damage.", piecePosition: { x: 50, y: 40 } },
    ],
  },
];

// ── Search engine ───────────────────────────────────────────────────

/** Fuzzy-ish local search over the built-in catalogue. */
export function localSearch(query: string): GameInfo[] {
  const q = query.toLowerCase().trim();
  if (!q) return GAMES.slice(0, 6);

  // Exact name match first
  const exact = GAMES.filter((g) => g.name.toLowerCase() === q);
  if (exact.length) return exact;

  // Prefix / includes match
  const matches = GAMES.filter(
    (g) =>
      g.name.toLowerCase().includes(q) ||
      g.description.toLowerCase().includes(q)
  );
  if (matches.length) return matches;

  // Fuzzy: any word overlap
  const words = q.split(/\s+/);
  const fuzzy = GAMES.filter((g) => {
    const text = `${g.name} ${g.description}`.toLowerCase();
    return words.some((w) => text.includes(w));
  });
  return fuzzy.length ? fuzzy : [];
}

/** Look up a single game by name (case-insensitive). */
export function getGameInfo(name: string): GameInfo | undefined {
  const n = name.toLowerCase().trim();
  return GAMES.find((g) => g.name.toLowerCase() === n);
}

/** All games in the catalogue. */
export function allGames(): GameInfo[] {
  return GAMES;
}
