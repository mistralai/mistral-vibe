//! Width allocation for Markdown tables.

use super::text::{cell_width, wrap_chars};
use super::Sc;

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
                .max(3)
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

fn shrink(
    natural: Vec<usize>,
    headers: &[Vec<Sc>],
    rows: &[Vec<Vec<Sc>>],
    total_space: usize,
) -> Vec<usize> {
    let minimums: Vec<usize> = (0..natural.len())
        .map(|column| {
            cells(headers, rows, column)
                .map(|cell| longest_word(cell).saturating_add(2))
                .max()
                .unwrap_or(3)
                .max(3)
                .min(natural[column])
        })
        .collect();
    let used = minimums.iter().sum::<usize>();
    if used >= total_space {
        return proportional(&minimums, total_space);
    }

    let pressure: Vec<Vec<usize>> = (0..natural.len())
        .map(|column| cells(headers, rows, column).map(cell_width).collect())
        .collect();
    let mut heights = Heights::new(headers, rows, &minimums);
    let mut widths = minimums;
    for _ in 0..total_space.saturating_sub(used) {
        let Some(column) = (0..widths.len())
            .filter(|&column| widths[column] < natural[column])
            .map(|column| {
                let height = heights.after_increment(column, widths[column]);
                let content_width = widths[column].saturating_sub(2).max(1) as f64;
                let pressure = pressure[column]
                    .iter()
                    .map(|width| *width as f64 / content_width)
                    .sum::<f64>();
                (column, height, pressure)
            })
            .min_by(|left, right| {
                left.1
                    .cmp(&right.1)
                    .then_with(|| right.2.total_cmp(&left.2))
                    .then_with(|| left.0.cmp(&right.0))
            })
            .map(|candidate| candidate.0)
        else {
            break;
        };
        widths[column] += 1;
        heights.increment(column, widths[column]);
    }
    widths
}

struct Heights<'a> {
    cells: Vec<Vec<Option<&'a [Sc]>>>,
    current: Vec<Vec<usize>>,
    next: Vec<Option<Vec<usize>>>,
}

impl<'a> Heights<'a> {
    fn new(headers: &'a [Vec<Sc>], rows: &'a [Vec<Vec<Sc>>], widths: &[usize]) -> Self {
        let cells: Vec<Vec<Option<&[Sc]>>> = std::iter::once(headers)
            .chain(rows.iter().map(Vec::as_slice))
            .map(|row| {
                (0..widths.len())
                    .map(|column| row.get(column).map(Vec::as_slice))
                    .collect()
            })
            .collect();
        let current = widths
            .iter()
            .enumerate()
            .map(|(column, &width)| column_heights(&cells, column, width))
            .collect();
        let next = (0..widths.len()).map(|_| None).collect();
        Self {
            cells,
            current,
            next,
        }
    }

    fn after_increment(&mut self, candidate: usize, width: usize) -> usize {
        if self.next[candidate].is_none() {
            self.next[candidate] = Some(column_heights(&self.cells, candidate, width + 1));
        }
        let candidate_heights = self.next[candidate]
            .as_ref()
            .expect("candidate heights exist");
        (0..self.cells.len())
            .map(|row| {
                self.current
                    .iter()
                    .enumerate()
                    .map(|(column, heights)| {
                        if column == candidate {
                            candidate_heights[row]
                        } else {
                            heights[row]
                        }
                    })
                    .max()
                    .unwrap_or(1)
            })
            .sum()
    }

    fn increment(&mut self, column: usize, width: usize) {
        self.current[column] = self.next[column]
            .take()
            .unwrap_or_else(|| column_heights(&self.cells, column, width));
    }
}

fn column_heights(cells: &[Vec<Option<&[Sc]>>], column: usize, width: usize) -> Vec<usize> {
    cells
        .iter()
        .map(|row| row[column].map_or(1, |cell| wrap_chars(cell, width.saturating_sub(2)).len()))
        .collect()
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
    cell.split(|(character, _)| character.is_whitespace())
        .map(cell_width)
        .max()
        .unwrap_or(0)
}
