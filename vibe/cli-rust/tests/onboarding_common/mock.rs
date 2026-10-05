//! A scriptable two-origin HTTP mock for the browser sign-in endpoints.
//! Included per test binary via `#[path]`.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// One HTTP origin: a std listener answering every request through the
/// route closure, one thread per connection. Fixed JSON payloads keep the
/// parser minimal; the gateway only needs the status line and the body.
pub struct MockOrigin {
    pub addr: SocketAddr,
}

impl MockOrigin {
    pub fn url(&self) -> String {
        format!("http://{}", self.addr)
    }
}

pub type Route = Arc<dyn Fn(&str, &str) -> (u16, String) + Send + Sync>;

fn bind_origin() -> (TcpListener, SocketAddr) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let addr = listener.local_addr().expect("local addr");
    (listener, addr)
}

fn serve_origin(listener: TcpListener, route: Route) {
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(stream) = stream else { return };
            let route = Arc::clone(&route);
            std::thread::spawn(move || serve(stream, route));
        }
    });
}

/// Parse just the request line and Content-Length worth of body.
fn serve(mut stream: TcpStream, route: Route) {
    let mut request = Vec::new();
    let mut chunk = [0u8; 4096];
    loop {
        let Ok(read) = stream.read(&mut chunk) else {
            return;
        };
        if read == 0 {
            return;
        }
        request.extend_from_slice(&chunk[..read]);
        let Some(headers_end) = request.windows(4).position(|w| w == b"\r\n\r\n") else {
            continue;
        };
        let length = String::from_utf8_lossy(&request[..headers_end])
            .lines()
            .find_map(|line| {
                let (name, value) = line.split_once(':')?;
                name.trim()
                    .eq_ignore_ascii_case("content-length")
                    .then(|| value.trim().parse::<usize>().ok())
                    .flatten()
            })
            .unwrap_or(0);
        if request.len() >= headers_end + 4 + length {
            break;
        }
    }
    let head = String::from_utf8_lossy(&request);
    let Some(request_line) = head.lines().next() else {
        return;
    };
    let mut parts = request_line.split(' ');
    let (Some(method), Some(path)) = (parts.next(), parts.next()) else {
        return;
    };
    let (status, body) = route(method, path);
    let reason = if status == 200 { "OK" } else { "Error" };
    let response = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes());
}

/// How the mock answers each sign-in step. `0` / `None` mean success; the
/// poll serves "pending" until approved, then "completed".
#[derive(Clone, Copy, Default)]
pub struct SignInScript {
    /// Non-zero answers create with this status instead of a process.
    pub create_status: u16,
    /// Fixed poll status ("expired", "denied", "error") instead of the
    /// approve-gated pending/completed pair.
    pub poll_status: Option<&'static str>,
    /// Non-zero answers exchange with this status instead of an api key.
    pub exchange_status: u16,
    /// Serves this JSON body at `*/vibe/whoami` when set, 404 otherwise.
    pub whoami: Option<&'static str>,
    /// Serves the sign-in URL on the answering origin, foreign to a
    /// console-only base.
    pub foreign_sign_in_url: bool,
}

#[derive(Clone, Copy)]
enum Role {
    /// Approves on any non-sign-in visit, like the sign-in page.
    Console,
    /// 404s anything it does not know.
    Api,
}

/// The sign-in endpoints, path-suffix matched so one origin serves both the
/// dual setup (API base at the root) and the single-domain setup (the
/// wizard derives `{console}/api` on the console origin itself).
fn scripted_route(
    self_addr: SocketAddr,
    console_addr: SocketAddr,
    script: SignInScript,
    approved: Arc<AtomicBool>,
    role: Role,
) -> Route {
    Arc::new(move |method: &str, path: &str| {
        if method == "POST" && path.ends_with("/vibe/sign-in") {
            if script.create_status != 0 {
                return (script.create_status, "{}".to_owned());
            }
            let sign_in_host = if script.foreign_sign_in_url {
                self_addr
            } else {
                console_addr
            };
            return (
                200,
                format!(
                    "{{\"process_id\": \"proc-1\", \
                     \"sign_in_url\": \"http://{sign_in_host}/sign-in?process_id=proc-1\", \
                     \"poll_url\": \"http://{self_addr}{path}/proc-1\", \
                     \"expires_at\": \"2999-01-01T00:00:00Z\"}}"
                ),
            );
        }
        if method == "GET" && path.ends_with("/vibe/sign-in/proc-1") {
            if let Some(status) = script.poll_status {
                let body = match status {
                    "expired" => {
                        "{\"status\": \"expired\", \"exchange_token\": null, \"message\": null}"
                    }
                    "denied" => {
                        "{\"status\": \"denied\", \"exchange_token\": null, \"message\": null}"
                    }
                    _ => {
                        "{\"status\": \"error\", \"exchange_token\": null, \
                         \"message\": \"Console says no.\"}"
                    }
                };
                return (200, body.to_owned());
            }
            if approved.load(Ordering::SeqCst) {
                return (
                    200,
                    "{\"status\": \"completed\", \"exchange_token\": \"tok-1\", \
                     \"message\": null}"
                        .to_owned(),
                );
            }
            return (
                200,
                "{\"status\": \"pending\", \"exchange_token\": null, \"message\": null}".to_owned(),
            );
        }
        if method == "POST" && path.ends_with("/vibe/sign-in/proc-1/exchange") {
            if script.exchange_status != 0 {
                return (script.exchange_status, "{}".to_owned());
            }
            return (200, "{\"api_key\": \"mock-key-1\"}".to_owned());
        }
        if method == "GET" && path.ends_with("/vibe/whoami") {
            if let Some(body) = script.whoami {
                return (200, body.to_owned());
            }
            return (404, "{}".to_owned());
        }
        match role {
            Role::Console => {
                // Any other visit approves the pending process, like the
                // manual mock's sign-in page.
                approved.fetch_or(true, Ordering::SeqCst);
                (200, "{\"ok\": true}".to_owned())
            }
            Role::Api => (404, "{}".to_owned()),
        }
    })
}

/// The dual-origin pair behind one script.
pub struct SignInMock {
    pub console: MockOrigin,
    pub api: MockOrigin,
    approved: Arc<AtomicBool>,
}

impl SignInMock {
    pub fn spawn(script: SignInScript) -> SignInMock {
        let approved = Arc::new(AtomicBool::new(false));
        let (console_listener, console_addr) = bind_origin();
        let (api_listener, api_addr) = bind_origin();

        serve_origin(
            console_listener,
            scripted_route(
                console_addr,
                console_addr,
                script,
                Arc::clone(&approved),
                Role::Console,
            ),
        );
        serve_origin(
            api_listener,
            scripted_route(
                api_addr,
                console_addr,
                script,
                Arc::clone(&approved),
                Role::Api,
            ),
        );

        SignInMock {
            console: MockOrigin { addr: console_addr },
            api: MockOrigin { addr: api_addr },
            approved,
        }
    }

    /// Approve the pending process, like visiting the sign-in page.
    pub fn approve(&self) {
        self.approved.store(true, Ordering::SeqCst);
    }

    pub fn is_approved(&self) -> bool {
        self.approved.load(Ordering::SeqCst)
    }
}
