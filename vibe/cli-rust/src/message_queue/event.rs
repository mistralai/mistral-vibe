//! Results of asynchronous prompt-queue requests.

use crate::server::ImageAttachment;

pub enum QueueEvent {
    Accepted {
        message_id: String,
        queue_item_id: String,
        session_id: String,
        images: Vec<ImageAttachment>,
    },
    Rejected {
        message_id: String,
        error: Option<String>,
    },
    GroupReplaced {
        server_message_id: String,
        message_ids: Vec<String>,
        images: Vec<ImageAttachment>,
        error: Option<String>,
    },
    SteerSettled {
        queue_item_id: String,
        accepted: Option<bool>,
    },
}
