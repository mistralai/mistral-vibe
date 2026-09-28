//! Snake spinner: a 3-4 dot snake random-walking a 4x4 braille grid.

use std::collections::HashSet;

use crate::ui::braille_renderer::render_braille;
use crate::ui::rng::Rng;

const MAP_WIDTH: i16 = 4;
const MAP_HEIGHT: i16 = 4;
const SNAKE_LENGTH: usize = 3;

/// A snake of lit dots walking a 4x4 grid. `positions[0]` is the head.
pub struct Snake {
    positions: Vec<(i16, i16)>,
    rng: Rng,
}

impl Default for Snake {
    fn default() -> Self {
        // Mirrors Python `[1, 0, 1j]` -> (x, y): head (1,0), (0,0), (0,1).
        Self {
            positions: vec![(1, 0), (0, 0), (0, 1)],
            rng: Rng::default(),
        }
    }
}

impl Snake {
    /// Advance one frame: step the snake, growing by one dot when it turns.
    pub fn tick(&mut self) {
        if self.positions.len() > SNAKE_LENGTH {
            self.positions.truncate(SNAKE_LENGTH);
            return;
        }
        let cur = self.current_direction();
        let dir = self.direction();
        let head = self.positions[0];
        let new_head = (head.0 + dir.0, head.1 + dir.1);
        self.positions.insert(0, new_head);
        if dir == cur {
            // Straight move keeps the length; a turn grows it by one.
            self.positions.pop();
        }
    }

    /// Render the lit dots as a braille grid (a 2-char glyph for a 4-wide field).
    pub fn render(&self) -> String {
        let dots: HashSet<(i16, i16)> = self.positions.iter().copied().collect();
        render_braille(&dots, MAP_WIDTH, MAP_HEIGHT)
    }

    fn current_direction(&self) -> (i16, i16) {
        let (h, n) = (self.positions[0], self.positions[1]);
        (h.0 - n.0, h.1 - n.1)
    }

    /// Pick the next step: straight while spanning both axes, else a random turn.
    fn direction(&mut self) -> (i16, i16) {
        let cur = self.current_direction();
        let head = self.positions[0];
        let ahead = (head.0 + cur.0, head.1 + cur.1);
        let spans_x = self.positions.iter().any(|p| p.0 != head.0);
        let spans_y = self.positions.iter().any(|p| p.1 != head.1);
        if spans_x && spans_y && in_bounds(ahead) {
            return cur;
        }
        let mut valid: Vec<(i16, i16)> = Vec::new();
        for offset in [cur, rotate_left(cur), rotate_right(cur)] {
            let np = (head.0 + offset.0, head.1 + offset.1);
            if in_bounds(np) && !self.positions.contains(&np) {
                valid.push(offset);
            }
        }
        if valid.is_empty() {
            return cur;
        }
        valid[(self.rng.next_u64() as usize) % valid.len()]
    }
}

fn in_bounds((x, y): (i16, i16)) -> bool {
    (0..MAP_WIDTH).contains(&x) && (0..MAP_HEIGHT).contains(&y)
}

/// Complex multiply by `1j`: (x, y) -> (-y, x).
fn rotate_left((x, y): (i16, i16)) -> (i16, i16) {
    (-y, x)
}

/// Complex multiply by `-1j`: (x, y) -> (y, -x).
fn rotate_right((x, y): (i16, i16)) -> (i16, i16) {
    (y, -x)
}
