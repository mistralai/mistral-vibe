//! Width allocation for Markdown tables.

use super::text::{cell_width, Words};
use super::Sc;

const MIN_WIDTH: usize = 3;
const STEPS: usize = 3;
/// Characters wrapped and cell heights read while climbing, so huge tables keep a bounded cost.
const BUDGET: usize = 40_000_000;
/// Budget charged per cached cell height, bounding the cache to about `BUDGET / 4` bytes.
const HEIGHT_COST: usize = 16;

pub(super) fn column_widths(
    headers: &[Vec<Sc>],
    rows: &[Vec<Vec<Sc>>],
    width: u16,
    pad: usize,
) -> Vec<usize> {
    let columns = headers.len();
    let natural: Vec<usize> = (0..columns)
        .map(|column| {
            cells(headers, rows, column)
                .map(cell_width)
                .max()
                .unwrap_or(0)
                .saturating_add(2)
                .max(MIN_WIDTH)
        })
        .collect();
    let used = natural.iter().sum::<usize>();
    let interior = (width as usize).saturating_sub(2 * pad + 2);
    let total_space = interior.saturating_sub(columns.saturating_sub(1));
    if total_space < used {
        return shrink(natural, headers, rows, total_space);
    }
    if used == 0 {
        return natural;
    }
    let (denominator, space) = (used as i128, total_space as i128);
    let mut widths = Vec::with_capacity(columns);
    let (mut run, mut previous) = (0i128, 0i128);
    for natural in natural {
        run += natural as i128 * space;
        let end = run / denominator;
        widths.push((end - previous) as usize);
        run += denominator;
        previous = run / denominator;
    }
    widths
}

/// Start from a proportional split, then move width between columns while it saves rows.
fn shrink(
    natural: Vec<usize>,
    headers: &[Vec<Sc>],
    rows: &[Vec<Vec<Sc>>],
    total_space: usize,
) -> Vec<usize> {
    let floor = MIN_WIDTH * natural.len();
    if total_space < floor {
        return proportional(&natural, total_space);
    }
    let slack: Vec<usize> = natural.iter().map(|width| width - MIN_WIDTH).collect();
    let start = proportional(&slack, total_space - floor)
        .into_iter()
        .map(|width| width + MIN_WIDTH)
        .collect();
    Fit::new(headers, rows, natural, total_space).climb(start)
}

/// How one column renders at one width: each row's height and its cells with a word split mid-word.
struct Shape {
    heights: Vec<u32>,
    broken: usize,
}

/// Rendered rows, cells with a word split mid-word, and wrapped cell lines: lower is better, in that order.
type Score = (usize, usize, usize);

struct Fit {
    grid: Vec<Vec<Option<(Words, usize)>>>,
    natural: Vec<usize>,
    shapes: Vec<Vec<Option<Shape>>>,
    weights: Vec<usize>,
    budget: usize,
}

impl Fit {
    fn new(
        headers: &[Vec<Sc>],
        rows: &[Vec<Vec<Sc>>],
        natural: Vec<usize>,
        total_space: usize,
    ) -> Self {
        let grid = std::iter::once(headers)
            .chain(rows.iter().map(Vec::as_slice))
            .map(|row| {
                (0..natural.len())
                    .map(|column| {
                        let cell = row.get(column)?;
                        Some((Words::new(cell), longest_word(cell)))
                    })
                    .collect()
            })
            .collect();
        let shapes = natural
            .iter()
            .map(|&width| (0..=width.min(total_space)).map(|_| None).collect())
            .collect();
        let weights = (0..natural.len())
            .map(|column| {
                cells(headers, rows, column)
                    .map(|cell| cell.len().max(1))
                    .sum()
            })
            .collect();
        Self {
            grid,
            natural,
            shapes,
            weights,
            budget: BUDGET,
        }
    }

    /// Hill-climb: apply the best width transfer between two columns until none improves.
    fn climb(&mut self, mut widths: Vec<usize>) -> Vec<usize> {
        let mut score = self.score(&widths);
        loop {
            let mut best: Option<(Score, usize, Vec<usize>)> = None;
            for grow in 0..widths.len() {
                for step in self.steps(grow, &widths) {
                    for shrink in (0..widths.len()).filter(|&column| column != grow) {
                        if widths[shrink] < MIN_WIDTH + step || self.budget == 0 {
                            continue;
                        }
                        let mut candidate = widths.clone();
                        candidate[grow] += step;
                        candidate[shrink] -= step;
                        let next = self.score(&candidate);
                        let better = best
                            .as_ref()
                            .is_none_or(|(best, best_step, _)| (next, step) < (*best, *best_step));
                        if next < score && better {
                            best = Some((next, step, candidate));
                        }
                    }
                }
            }
            let Some((next, _, candidate)) = best else {
                return widths;
            };
            score = next;
            widths = candidate;
        }
    }

    /// The next few widenings of `column` that change how it wraps, within what one other column can give.
    fn steps(&mut self, column: usize, widths: &[usize]) -> Vec<usize> {
        let room = (0..widths.len())
            .filter(|&other| other != column)
            .map(|other| widths[other] - MIN_WIDTH)
            .max()
            .unwrap_or(0);
        let from = widths[column];
        let to = self.natural[column].min(from + room);
        (from + 1..=to)
            .filter(|&width| self.budget > 0 && self.wraps_differently(column, width))
            .take(STEPS)
            .map(|width| width - from)
            .collect()
    }

    fn wraps_differently(&mut self, column: usize, width: usize) -> bool {
        self.budget = self.budget.saturating_sub(self.grid.len());
        self.shape(column, width - 1);
        self.shape(column, width);
        let [narrower, wider] =
            [width - 1, width].map(|width| self.shapes[column][width].as_ref().expect("cached"));
        narrower.heights != wider.heights || narrower.broken != wider.broken
    }

    fn score(&mut self, widths: &[usize]) -> Score {
        self.budget = self.budget.saturating_sub(widths.len() * self.grid.len());
        let mut heights = vec![1; self.grid.len()];
        let (mut broken, mut lines) = (0, 0);
        for (column, &width) in widths.iter().enumerate() {
            let shape = self.shape(column, width);
            for (height, &cell) in heights.iter_mut().zip(&shape.heights) {
                *height = (*height).max(cell as usize);
            }
            broken += shape.broken;
            lines += shape
                .heights
                .iter()
                .map(|&height| height as usize)
                .sum::<usize>();
        }
        (heights.iter().sum(), broken, lines)
    }

    fn shape(&mut self, column: usize, width: usize) -> &Shape {
        let (grid, budget, weight) = (&self.grid, &mut self.budget, self.weights[column]);
        self.shapes[column][width].get_or_insert_with(|| {
            *budget = budget.saturating_sub(weight + HEIGHT_COST * grid.len());
            let region = width.saturating_sub(2);
            let cells = grid.iter().filter_map(|row| row[column].as_ref());
            Shape {
                heights: grid
                    .iter()
                    .map(|row| {
                        let height = row[column].as_ref().map_or(1, |cell| cell.0.height(region));
                        u32::try_from(height).unwrap_or(u32::MAX)
                    })
                    .collect(),
                broken: cells.filter(|cell| cell.1 > region).count(),
            }
        })
    }
}

fn cells<'a>(
    headers: &'a [Vec<Sc>],
    rows: &'a [Vec<Vec<Sc>>],
    column: usize,
) -> impl Iterator<Item = &'a [Sc]> {
    std::iter::once(headers[column].as_slice()).chain(
        rows.iter()
            .filter_map(move |row| row.get(column).map(Vec::as_slice)),
    )
}

fn proportional(weights: &[usize], total: usize) -> Vec<usize> {
    let sum = weights.iter().sum::<usize>().max(1);
    let mut run = 0usize;
    let mut previous = 0usize;
    weights
        .iter()
        .map(|weight| {
            run += weight * total;
            let end = run / sum;
            let width = end.saturating_sub(previous);
            previous = end;
            width
        })
        .collect()
}

fn longest_word(cell: &[Sc]) -> usize {
    cell.split(|sc| sc.0.is_whitespace())
        .map(cell_width)
        .max()
        .unwrap_or(0)
}

#[cfg(test)]
mod tests;
