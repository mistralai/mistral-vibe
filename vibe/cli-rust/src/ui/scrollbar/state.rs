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

/// Where a track click lands relative to the thumb.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    Above,
    Below,
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

    pub fn area(&self) -> Option<Rect> {
        self.geometry.map(|geometry| geometry.area)
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

    /// Track side of `at` relative to the thumb; `None` on the thumb or outside the track.
    pub fn track_side(&self, at: (u16, u16)) -> Option<Side> {
        let geometry = self.geometry?;
        let row = geometry.track_row(at)?;
        let (start, end) = geometry.thumb();
        if row < start {
            return Some(Side::Above);
        }
        (row >= end).then_some(Side::Below)
    }

    /// Adopt `live` geometry while keeping this state's position, clamped to the new bounds.
    pub fn follow(&mut self, live: State) {
        let position = self.geometry.map(|geometry| geometry.position);
        self.geometry = live.geometry.map(|mut geometry| {
            if let Some(position) = position {
                let max = geometry.virtual_size.saturating_sub(geometry.window_size);
                geometry.position = position.min(max);
            }
            geometry
        });
    }

    /// Move one page toward `side` (Textual `scroll_page_up`/`down`) and return the new position.
    pub fn page(&mut self, side: Side) -> Option<usize> {
        let geometry = self.geometry.as_mut()?;
        let max = geometry.virtual_size.saturating_sub(geometry.window_size);
        let position = match side {
            Side::Above => geometry.position.saturating_sub(geometry.window_size),
            Side::Below => geometry.position.saturating_add(geometry.window_size),
        };
        geometry.position = position.min(max);
        Some(geometry.position)
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
    fn hits_thumb(self, at: (u16, u16)) -> bool {
        let (start, end) = self.thumb();
        self.track_row(at)
            .is_some_and(|row| row >= start && row < end)
    }

    fn track_row(self, at: (u16, u16)) -> Option<u16> {
        self.area.contains(at.into()).then(|| at.1 - self.area.y)
    }

    fn thumb(self) -> (u16, u16) {
        thumb_rows(
            self.area.height,
            self.virtual_size,
            self.window_size,
            self.position,
        )
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
