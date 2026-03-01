export type GameType = 'chess' | 'mahjong' | 'generic';

export interface GameRules {
  gameType: GameType;
}

export interface ValidationResult {
  ok: boolean;
  reason?: string;
}

export class GameEngineServer {
  private readonly rules = new Map<string, GameRules>();

  loadRules(gameId: string, rules: GameRules): void {
    this.rules.set(gameId, rules);
  }

  validateMove(gameId: string): ValidationResult {
    if (!this.rules.has(gameId)) {
      return { ok: false, reason: 'Rules not loaded' };
    }
    return { ok: true };
  }
}
