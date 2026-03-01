export interface Evaluation {
  score: number;
  motifs: string[];
}

export class PositionEvaluator {
  evaluateChess(): Evaluation {
    return { score: 0, motifs: [] };
  }

  evaluateMahjong(): Evaluation {
    return { score: 0, motifs: [] };
  }
}
