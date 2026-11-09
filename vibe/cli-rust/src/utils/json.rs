//! Tolerant typed reads of app-server JSON (ADR 0014).

use serde::de::DeserializeOwned;
use serde::{Deserialize, Deserializer};
use serde_json::Value;

/// `value` read as `T`, or `T::default()` when it is null or its shape does not match.
pub fn read<'a, T: Deserialize<'a> + Default>(value: &'a Value) -> T {
    if value.is_null() {
        return T::default();
    }
    T::deserialize(value).unwrap_or_else(|error| {
        let shape = std::any::type_name::<T>();
        tracing::warn!(%error, shape, "wire shape mismatch, using defaults");
        T::default()
    })
}

/// `deserialize_with` reading `null` as `T::default()`.
pub fn null_default<'de, D: Deserializer<'de>, T: Deserialize<'de> + Default>(
    d: D,
) -> Result<T, D::Error> {
    Ok(Option::<T>::deserialize(d)?.unwrap_or_default())
}

/// `deserialize_with` for a list that skips malformed items instead of failing.
pub fn lenient<'de, D: Deserializer<'de>, T: DeserializeOwned>(d: D) -> Result<Vec<T>, D::Error> {
    Ok(lenient_opt(d)?.unwrap_or_default())
}

/// `lenient`, but `None` when the value is not a list.
pub fn lenient_opt<'de, D: Deserializer<'de>, T: DeserializeOwned>(
    d: D,
) -> Result<Option<Vec<T>>, D::Error> {
    let Value::Array(items) = Value::deserialize(d)? else {
        return Ok(None);
    };
    Ok(Some(
        items
            .into_iter()
            .filter_map(|item| T::deserialize(item).ok())
            .collect(),
    ))
}
