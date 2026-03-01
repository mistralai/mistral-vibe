/**
 * API client for the BoardGame AI Moderator backend.
 *
 * All AI calls go through the FastAPI backend at `VITE_API_URL`
 * (defaults to http://localhost:8000).
 */

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Game search & detail ────────────────────────────────────────────

export interface SearchResult {
  name: string;
  player_count: string;
  complexity: number;
  description: string;
}

export async function searchGames(query: string): Promise<SearchResult[]> {
  const data = await apiFetch<{ results: SearchResult[] }>(
    `/api/games/search?q=${encodeURIComponent(query)}`
  );
  return data.results;
}

export async function getGameDetail(gameName: string) {
  return apiFetch<{ id: number; schema: Record<string, unknown> }>(
    `/api/games/${encodeURIComponent(gameName)}`
  );
}

// ── Rule explanation ────────────────────────────────────────────────

export async function explainGame(
  gameName: string,
  mode: "quickstart" | "stepbystep" | "simulation" = "quickstart"
) {
  return apiFetch<{ game_name: string; mode: string; explanation: string }>(
    `/api/moderation/explain/${encodeURIComponent(gameName)}?mode=${mode}`
  );
}

export async function simulateGame(gameName: string) {
  return apiFetch<{ game_name: string; simulation: string }>(
    `/api/moderation/simulate/${encodeURIComponent(gameName)}`
  );
}

// ── Rule Q&A ────────────────────────────────────────────────────────

export interface RuleAnswer {
  answer: string;
  citation: string | null;
  has_house_rule_conflict: boolean;
  official_rule: string;
  house_rule_override: string;
}

export async function askRule(
  gameName: string,
  question: string,
  houseRules: string[] = []
): Promise<RuleAnswer> {
  return apiFetch<RuleAnswer>("/api/rules/qa", {
    method: "POST",
    body: JSON.stringify({
      game_name: gameName,
      question,
      house_rules: houseRules,
    }),
  });
}

// ── House rules ─────────────────────────────────────────────────────

export interface HouseRuleValidation {
  is_valid: boolean;
  contradictions: { conflicting_rule: string; reason: string; severity: string }[];
  impact_summary: string;
  balance_impact: string;
  length_impact: string;
  complexity_impact: string;
}

export async function validateHouseRule(
  gameName: string,
  proposedRule: string,
  existingRules: string[] = []
): Promise<HouseRuleValidation> {
  return apiFetch<HouseRuleValidation>("/api/rules/house-rules/validate", {
    method: "POST",
    body: JSON.stringify({
      game_name: gameName,
      proposed_rule: proposedRule,
      existing_house_rules: existingRules,
    }),
  });
}

// ── Dispute resolution ──────────────────────────────────────────────

export interface DisputeRuling {
  title: string;
  body: string;
  source: string;
  citation: string | null;
  confidence: number;
}

export async function resolveDispute(
  gameName: string,
  description: string,
  playersInvolved: string[] = []
): Promise<DisputeRuling> {
  return apiFetch<DisputeRuling>("/api/rules/dispute", {
    method: "POST",
    body: JSON.stringify({
      game_name: gameName,
      description,
      players_involved: playersInvolved,
    }),
  });
}

// ── Moderation ──────────────────────────────────────────────────────

export interface MoveValidation {
  is_valid: boolean;
  explanation: string;
  rule_reference: string | null;
}

export async function validateMove(
  gameName: string,
  moveDescription: string,
  gameState: Record<string, unknown> = {},
  houseRules: string[] = []
): Promise<MoveValidation> {
  return apiFetch<MoveValidation>("/api/moderation/validate-move", {
    method: "POST",
    body: JSON.stringify({
      game_name: gameName,
      move_description: moveDescription,
      game_state: gameState,
      house_rules: houseRules,
    }),
  });
}

// ── Voice transcription ─────────────────────────────────────────────

export async function transcribeAudio(audioBlob: Blob): Promise<string> {
  const form = new FormData();
  form.append("file", audioBlob, "recording.webm");

  const res = await fetch(`${API_BASE}/api/voice/transcribe`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(`Transcription failed: ${res.status}`);
  const data = await res.json();
  return data.text;
}

// ── Sessions ────────────────────────────────────────────────────────

export async function createSession(gameId: number, players: { id: string; name: string; color: string }[]) {
  return apiFetch<Record<string, unknown>>("/api/sessions/", {
    method: "POST",
    body: JSON.stringify({ game_id: gameId, players }),
  });
}

export async function advanceTurn(sessionId: number) {
  return apiFetch<Record<string, unknown>>("/api/sessions/advance-turn", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export async function updateScore(sessionId: number, playerId: string, delta: number) {
  return apiFetch<Record<string, unknown>>("/api/sessions/update-score", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, player_id: playerId, delta }),
  });
}

export async function finishSession(sessionId: number) {
  return apiFetch<Record<string, unknown>>(`/api/sessions/${sessionId}/finish`, {
    method: "POST",
  });
}

// ── Health check ────────────────────────────────────────────────────

export async function healthCheck(): Promise<{ status: string; api_key_configured: boolean }> {
  return apiFetch("/api/health");
}
