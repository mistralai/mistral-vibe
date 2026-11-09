//! `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings`
//! access, isolated so the parsing stays testable on every platform.

use windows_sys::Win32::Foundation::ERROR_SUCCESS;
use windows_sys::Win32::System::Registry::{
    RegCloseKey, RegOpenKeyExW, RegQueryValueExW, HKEY, HKEY_CURRENT_USER, KEY_QUERY_VALUE,
    REG_DWORD, REG_SZ, REG_VALUE_TYPE,
};

const SUBKEY: &str = "Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings";

/// An open registry key, closed on drop.
pub struct InternetSettings(HKEY);

impl InternetSettings {
    /// The per-user Internet Settings key, or `None` when it cannot be opened.
    pub fn open() -> Option<Self> {
        let subkey: Vec<u16> = SUBKEY.encode_utf16().chain([0]).collect();
        let mut key = std::ptr::null_mut();
        // Safety: `key` is a valid out-pointer for the whole call.
        let status = unsafe {
            RegOpenKeyExW(
                HKEY_CURRENT_USER,
                subkey.as_ptr(),
                0,
                KEY_QUERY_VALUE,
                &mut key,
            )
        };
        (status == ERROR_SUCCESS).then_some(Self(key))
    }

    /// The `REG_SZ` value `name`, or `None` when absent or of another type.
    pub fn string_value(&self, name: &str) -> Option<String> {
        let bytes = self.query_value(name, REG_SZ)?;
        let units: Vec<u16> = bytes
            .chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect();
        Some(
            String::from_utf16_lossy(&units)
                .trim_end_matches('\0')
                .to_owned(),
        )
    }

    /// The `REG_DWORD` value `name`, false when absent, of another type, or zero.
    pub fn bool_value(&self, name: &str) -> bool {
        self.query_value(name, REG_DWORD)
            .is_some_and(|bytes| bytes.iter().any(|byte| *byte != 0))
    }

    /// The value's raw bytes when its type is `expected`; absent or mistyped values are `None`.
    fn query_value(&self, name: &str, expected: REG_VALUE_TYPE) -> Option<Vec<u8>> {
        let wide: Vec<u16> = name.encode_utf16().chain([0]).collect();
        let mut value_type = 0;
        let mut size = 0;
        // Safety: the pointers are valid out-parameters for the whole call.
        let mut status = unsafe {
            RegQueryValueExW(
                self.0,
                wide.as_ptr(),
                std::ptr::null(),
                &mut value_type,
                std::ptr::null_mut(),
                &mut size,
            )
        };
        if status != ERROR_SUCCESS || value_type != expected {
            return None;
        }
        let mut buffer = vec![0u8; size as usize];
        // Safety: `buffer` has `size` bytes for the whole call.
        status = unsafe {
            RegQueryValueExW(
                self.0,
                wide.as_ptr(),
                std::ptr::null(),
                std::ptr::null_mut(),
                buffer.as_mut_ptr(),
                &mut size,
            )
        };
        (status == ERROR_SUCCESS).then_some(buffer)
    }
}

impl Drop for InternetSettings {
    fn drop(&mut self) {
        // Safety: the key came from `RegOpenKeyExW` and is closed exactly once.
        unsafe { RegCloseKey(self.0) };
    }
}
