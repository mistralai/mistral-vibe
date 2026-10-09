//! Wire types for `/proxy-setup`.

use std::collections::HashMap;

use serde::Deserialize;
use serde_json::{Map, Value};

#[derive(Debug, Deserialize)]
pub struct ConfigProxyReadResponse {
    pub settings: ProxySettingsView,
}

/// Python `ProxySettingsView`; `descriptions` keeps the server's variable order.
#[derive(Debug, Deserialize)]
pub struct ProxySettingsView {
    #[serde(default)]
    pub values: HashMap<String, Option<String>>,
    pub descriptions: Map<String, Value>,
}
