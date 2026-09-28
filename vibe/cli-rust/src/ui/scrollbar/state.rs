//! Scrollbar geometry and drag state.

use ratatui::layout::Rect;

#[derive(Clone, Copy)]
struct Geometry {
    area: Rect,
    virtual_size: usize,
    window_size: usize,
    position: usize,
}

#[derive(Clone, Copy)]
struct Drag {
    row: u16,
    position: usize,
}

#[derive(Clone, Copy, Default)]
pub struct State {
    geometry: Option<Geometry>,
    drag: Option<Drag>,
}

impl State {
    pub fn clear(&mut self) {
        self.geometry = None;
        self.drag = None;
    }

    pub fn update(&mut self, area: Rect, virtual_size: u16, window_size: u16, position: u16) {
        self.update_large(
            area,
            usize::from(virtual_size),
            usize::from(window_size),
            usize::from(position),
        );
    }

    pub fn update_large(
        &mut self,
        area: Rect,
        virtual_size: usize,
        window_size: usize,
        position: usize,
    ) {
        self.geometry = Some(Geometry {
            area,
            virtual_size,
            window_size,
            position,
        });
    }

    pub fn max_scroll(&self) -> Option<u16> {
        self.max_scroll_large()
            .map(|max| u16::try_from(max).unwrap_or(u16::MAX))
    }

    pub fn max_scroll_large(&self) -> Option<usize> {
        self.geometry
            .map(|geometry| geometry.virtual_size.saturating_sub(geometry.window_size))
    }

    pub fn contains(&self, at: (u16, u16)) -> bool {
        self.geometry
            .is_some_and(|geometry| geometry.area.contains(at.into()))
    }

    pub fn begin_drag(&mut self, at: (u16, u16)) -> bool {
        let Some(geometry) = self.geometry.filter(|geometry| geometry.hits_thumb(at)) else {
            return false;
        };
        self.drag = Some(Drag {
            row: at.1,
            position: geometry.position,
        });
        true
    }

    pub fn drag_to(&self, row: u16) -> Option<usize> {
        let (geometry, drag) = (self.geometry?, self.drag?);
        let delta = f64::from(row) - f64::from(drag.row);
        let max = geometry.virtual_size.saturating_sub(geometry.window_size);
        let position = (drag.position as f64
            + delta * geometry.virtual_size as f64 / geometry.window_size as f64)
            .round_ties_even()
            .clamp(0.0, max as f64) as usize;
        Some(position)
    }

    pub fn selection_edge_scroll(&self, row: i32) -> i8 {
        const EDGE_ROWS: u16 = 3;

        let Some(area) = self.geometry.map(|geometry| geometry.area) else {
            return 0;
        };
        let top = i32::from(area.y);
        let bottom = i32::from(area.bottom());
        if row < top {
            return -(EDGE_ROWS as i8);
        }
        if row >= bottom {
            return EDGE_ROWS as i8;
        }
        let row = row as u16;
        let top_distance = row - area.y;
        if top_distance < EDGE_ROWS {
            return -((EDGE_ROWS - top_distance) as i8);
        }
        let bottom_distance = area.bottom() - row - 1;
        if bottom_distance < EDGE_ROWS {
            return (EDGE_ROWS - bottom_distance) as i8;
        }
        0
    }
}

impl Geometry {
    fn hits_thumb(self, (column, row): (u16, u16)) -> bool {
        if column < self.area.x
            || column >= self.area.right()
            || row < self.area.y
            || row >= self.area.bottom()
        {
            return false;
        }
        let (start, end) = thumb_rows(
            self.area.height,
            self.virtual_size,
            self.window_size,
            self.position,
        );
        let local_row = row - self.area.y;
        local_row >= start && local_row < end
    }
}

fn thumb_rows(size: u16, virtual_size: usize, window_size: usize, position: usize) -> (u16, u16) {
    let size = f64::from(size);
    let thumb_size = (window_size as f64 * size / virtual_size as f64).max(1.0);
    let max_position = (virtual_size - window_size) as f64;
    let start = ((size - thumb_size) * position as f64 / max_position * 8.0) as u16;
    let length = (thumb_size * 8.0).ceil() as u16;
    (start / 8, (start + length).div_ceil(8))
}
