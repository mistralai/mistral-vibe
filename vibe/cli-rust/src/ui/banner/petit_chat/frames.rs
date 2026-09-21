//! Dot sets for the animated cat's 26-step transition cycle.

pub(super) const STARTING_DOTS: [&[i16]; 12] = [
    &[],
    &[6, 7, 15, 19],
    &[5, 8, 14, 16, 18, 20],
    &[4, 6, 7, 14, 17, 20],
    &[3, 5, 10, 11, 12, 14, 20],
    &[3, 5, 9, 13, 14, 16, 18, 20],
    &[3, 5, 8, 13, 17, 21],
    &[3, 6, 7, 8, 11, 14, 15, 16, 18, 19, 20],
    &[4, 5, 8, 12, 17, 19],
    &[6, 7, 8, 13, 18, 20],
    &[9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
    &[],
];

pub(super) struct Transition {
    pub remove: &'static [(i16, i16)],
    pub add: &'static [(i16, i16)],
}

const QUEUE_RIGHT_TO_MID_REMOVE: &[(i16, i16)] = &[
    (6, 1),
    (7, 1),
    (8, 2),
    (4, 3),
    (6, 3),
    (7, 3),
    (4, 8),
    (5, 8),
];
const QUEUE_RIGHT_TO_MID_ADD: &[(i16, i16)] = &[
    (4, 1),
    (3, 2),
    (3, 3),
    (5, 3),
    (5, 7),
    (3, 8),
    (4, 9),
    (5, 9),
];
const QUEUE_MID_TO_LEFT_REMOVE: &[(i16, i16)] = &[
    (4, 1),
    (5, 2),
    (3, 3),
    (5, 3),
    (5, 7),
    (3, 8),
    (4, 9),
    (5, 9),
];
const QUEUE_MID_TO_LEFT_ADD: &[(i16, i16)] = &[
    (1, 1),
    (2, 1),
    (0, 2),
    (1, 3),
    (2, 3),
    (4, 3),
    (4, 8),
    (5, 8),
];

const HEAD_RIGHT_REMOVE: &[(i16, i16)] = &[(16, 5), (18, 5), (17, 6)];
const HEAD_RIGHT_ADD: &[(i16, i16)] = &[(17, 5), (19, 5), (18, 6)];
const HEAD_DOWN_REMOVE: &[(i16, i16)] = &[
    (15, 1),
    (19, 1),
    (14, 2),
    (16, 2),
    (18, 2),
    (20, 2),
    (17, 3),
    (17, 5),
    (19, 5),
    (13, 6),
    (18, 6),
    (21, 6),
    (14, 7),
    (15, 7),
    (16, 7),
    (19, 7),
    (20, 7),
];
const HEAD_DOWN_ADD: &[(i16, i16)] = &[
    (15, 2),
    (19, 2),
    (16, 3),
    (18, 3),
    (17, 4),
    (14, 6),
    (17, 6),
    (19, 6),
    (20, 6),
    (13, 7),
    (18, 7),
    (21, 7),
    (14, 8),
    (15, 8),
    (16, 8),
    (18, 8),
    (20, 8),
];
const HEAD_UP_ADD: &[(i16, i16)] = &[
    (15, 1),
    (19, 1),
    (14, 2),
    (16, 2),
    (18, 2),
    (20, 2),
    (17, 3),
    (17, 5),
    (19, 5),
    (13, 6),
    (18, 6),
    (21, 6),
    (14, 7),
    (15, 7),
    (16, 7),
    (18, 7),
    (19, 7),
    (20, 7),
];

const BLINK_HIGH: &[(i16, i16)] = &[(16, 5), (18, 5)];
const BLINK_LOW: &[(i16, i16)] = &[(17, 6), (19, 6)];
const EMPTY: &[(i16, i16)] = &[];

pub(super) const TRANSITIONS: &[Transition] = &[
    Transition {
        remove: BLINK_HIGH,
        add: EMPTY,
    },
    Transition {
        remove: EMPTY,
        add: BLINK_HIGH,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_RIGHT_TO_MID_REMOVE,
        add: QUEUE_RIGHT_TO_MID_ADD,
    },
    Transition {
        remove: HEAD_RIGHT_REMOVE,
        add: HEAD_RIGHT_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_MID_TO_LEFT_REMOVE,
        add: QUEUE_MID_TO_LEFT_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_MID_TO_LEFT_ADD,
        add: QUEUE_MID_TO_LEFT_REMOVE,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: HEAD_DOWN_REMOVE,
        add: HEAD_DOWN_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_RIGHT_TO_MID_ADD,
        add: QUEUE_RIGHT_TO_MID_REMOVE,
    },
    Transition {
        remove: BLINK_LOW,
        add: EMPTY,
    },
    Transition {
        remove: EMPTY,
        add: BLINK_LOW,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_RIGHT_TO_MID_REMOVE,
        add: QUEUE_RIGHT_TO_MID_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_MID_TO_LEFT_REMOVE,
        add: QUEUE_MID_TO_LEFT_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: HEAD_DOWN_ADD,
        add: HEAD_UP_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_MID_TO_LEFT_ADD,
        add: QUEUE_MID_TO_LEFT_REMOVE,
    },
    Transition {
        remove: HEAD_RIGHT_ADD,
        add: HEAD_RIGHT_REMOVE,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_RIGHT_TO_MID_ADD,
        add: QUEUE_RIGHT_TO_MID_REMOVE,
    },
];
