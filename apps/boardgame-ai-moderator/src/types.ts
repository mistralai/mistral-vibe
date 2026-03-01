export enum PlayerColor {
  RED = 'RED',
  BLUE = 'BLUE',
  YELLOW = 'YELLOW',
  GREEN = 'GREEN'
}

export interface Player {
  id: string;
  name: string;
  color: PlayerColor;
  score: number;
  isCurrentTurn: boolean;
}

export interface GameState {
  gameName: string;
  players: Player[];
  turnCount: number;
  status: 'setup' | 'playing' | 'finished';
  history: string[];
}

export interface ChatMessage {
  role: 'user' | 'model' | 'system';
  text: string;
  timestamp: number;
}
