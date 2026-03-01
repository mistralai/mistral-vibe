export interface SimilarPosition {
  id: string;
  winRate: number;
}

export class StrategySearch {
  findSimilarPositions(limit = 5): SimilarPosition[] {
    return Array.from({ length: limit }, (_, index) => ({ id: `pos-${index + 1}`, winRate: 0.5 }));
  }
}
