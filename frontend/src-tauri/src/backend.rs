use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::PathBuf,
    sync::Mutex,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};
use uuid::Uuid;

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8001;
const SIDECAR_NAME: &str = "video-course-cards-backend";
const HEALTH_TIMEOUT: Duration = Duration::from_secs(1);
const STARTUP_TIMEOUT: Duration = Duration::from_secs(45);
const POLL_INTERVAL: Duration = Duration::from_millis(300);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(5);
const APP_DATA_DIR_NAME: &str = "Video Course Cards";
const APPLICATION_ID: &str = "video-course-cards";
const INSTANCE_TOKEN_HEADER: &str = "X-VCC-Instance-Token";

struct OwnedBackend<C = CommandChild> {
    child: Option<C>,
    pid: u32,
    instance_token: String,
}

pub struct BackendState {
    owned: Mutex<Option<OwnedBackend>>,
    last_message: Mutex<String>,
}

impl BackendState {
    pub fn new() -> Self {
        Self {
            owned: Mutex::new(None),
            last_message: Mutex::new("Backend not started.".to_string()),
        }
    }

    fn set_message(&self, message: impl Into<String>) {
        if let Ok(mut last_message) = self.last_message.lock() {
            *last_message = message.into();
        }
    }

    fn message(&self) -> String {
        self.last_message
            .lock()
            .map(|message| message.clone())
            .unwrap_or_else(|_| "Backend state unavailable.".to_string())
    }
}

impl Drop for BackendState {
    fn drop(&mut self) {
        if let Ok(mut owned) = self.owned.lock() {
            if let Some(mut process) = owned.take() {
                let quiesced = request_backend_quiesce(&process.instance_token).is_ok();
                if !quiesced {
                    eprintln!(
                        "backend sidecar did not quiesce before app exit; \
                         forcing durable restart recovery"
                    );
                }
                if let Err(error) = terminate_owned_process(&process, terminate_process) {
                    eprintln!(
                        "backend sidecar could not be terminated by pid: {error}; \
                         falling back to the owned child handle"
                    );
                    if let Some(child) = process.child.take() {
                        let _ = child.kill();
                    }
                }
            }
        }
    }
}

#[derive(Debug, Deserialize)]
struct HealthPayload {
    status: String,
    application_id: String,
    api_version: u32,
    instance_token: Option<String>,
}

#[derive(Debug, Deserialize)]
struct QuiescePayload {
    status: String,
    instance_token: String,
}

#[derive(Clone, Serialize)]
pub struct BackendStatus {
    ready: bool,
    mode: String,
    message: String,
    application_id: String,
    api_version: u32,
    identity_verified: bool,
}

#[tauri::command]
pub fn get_backend_status(state: State<'_, BackendState>) -> BackendStatus {
    let (ready, mode) = owned_identity(&state)
        .map(|(_, token)| (is_backend_ready_for_token(Some(&token)), "sidecar"))
        .unwrap_or_else(|| {
            let ready = is_backend_ready_for_token(None);
            (ready, if ready { "external" } else { "stopped" })
        });

    BackendStatus {
        ready,
        mode: mode.to_string(),
        message: state.message(),
        application_id: APPLICATION_ID.to_string(),
        api_version: 1,
        identity_verified: ready,
    }
}

#[tauri::command]
pub fn ensure_backend(
    app: AppHandle,
    state: State<'_, BackendState>,
) -> Result<BackendStatus, String> {
    ensure_backend_inner(&app, &state, false)
}

#[tauri::command]
pub fn restart_backend(
    app: AppHandle,
    state: State<'_, BackendState>,
) -> Result<BackendStatus, String> {
    if !has_owned_backend(&state) {
        if is_backend_ready_for_token(None) {
            return Err(
                "The backend on port 8001 is externally managed and was left running.".to_string(),
            );
        }
        if is_backend_port_reachable() {
            return Err(unrelated_port_message());
        }
    }
    stop_owned_backend(&state)?;
    ensure_backend_inner(&app, &state, true)
}

#[tauri::command]
pub fn stop_backend(state: State<'_, BackendState>) -> Result<BackendStatus, String> {
    if !has_owned_backend(&state) {
        state.set_message(
            "No owned backend sidecar was stopped; external services were left unchanged.",
        );
        return Ok(get_backend_status(state));
    }
    stop_owned_backend(&state)?;
    state.set_message("Stopped owned backend sidecar.");

    Ok(get_backend_status(state))
}

fn ensure_backend_inner(
    app: &AppHandle,
    state: &BackendState,
    force_spawn: bool,
) -> Result<BackendStatus, String> {
    let owned = owned_identity(state);
    let expected_token = owned.as_ref().map(|(_, token)| token.as_str());
    if !force_spawn && is_backend_ready_for_token(expected_token) {
        let mode = if owned.is_some() {
            "sidecar"
        } else {
            "external"
        };
        let message = format!("Backend ready at {}.", backend_base_url());
        state.set_message(message.clone());
        return Ok(BackendStatus {
            ready: true,
            mode: mode.to_string(),
            message,
            application_id: APPLICATION_ID.to_string(),
            api_version: 1,
            identity_verified: true,
        });
    }

    if let Some((_, token)) = owned {
        state.set_message("Waiting for owned backend sidecar to become ready.");
        return wait_for_ready_status(state, "sidecar", Some(&token));
    }

    if is_backend_port_reachable() {
        let message = unrelated_port_message();
        state.set_message(message.clone());
        return Err(message);
    }

    start_sidecar(app, state)?;
    let token = owned_identity(state)
        .map(|(_, token)| token)
        .ok_or_else(|| "Backend ownership was lost after spawn.".to_string())?;
    wait_for_ready_status(state, "sidecar", Some(&token))
}

fn start_sidecar(app: &AppHandle, state: &BackendState) -> Result<(), String> {
    state.set_message("Starting backend sidecar.");
    let app_data_dir = app_data_dir()?;
    let log_dir = app_data_dir.join("logs");
    std::fs::create_dir_all(&log_dir)
        .map_err(|error| format!("Failed to create backend log directory: {error}"))?;
    let log_file = log_dir.join("backend.log");
    let data_dir = app_data_dir.to_string_lossy().to_string();
    let log_file = log_file.to_string_lossy().to_string();
    let instance_token = Uuid::new_v4().to_string();

    let sidecar = app
        .shell()
        .sidecar(SIDECAR_NAME)
        .map_err(|error| format!("Failed to prepare backend sidecar: {error}"))?
        .env("VCC_DESKTOP", "1")
        .env("VCC_DATA_DIR", &data_dir)
        .env("VCC_BACKEND_LOG_FILE", &log_file)
        .env("VCC_BACKEND_INSTANCE_TOKEN", &instance_token)
        .args([
            "--host",
            BACKEND_HOST,
            "--port",
            &BACKEND_PORT.to_string(),
            "--no-reuse-existing",
            "--desktop",
            "--log-file",
            &log_file,
        ]);

    let (mut receiver, child) = sidecar
        .spawn()
        .map_err(|error| format!("Failed to spawn backend sidecar: {error}"))?;
    let pid = child.pid();

    {
        let mut owned = state
            .owned
            .lock()
            .map_err(|_| "Backend process lock is poisoned.".to_string())?;
        *owned = Some(OwnedBackend {
            child: Some(child),
            pid,
            instance_token: instance_token.clone(),
        });
    }

    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = receiver.recv().await {
            println!("backend sidecar event: {event:?}");
            if matches!(event, CommandEvent::Terminated(_)) {
                let backend_state = app_handle.state::<BackendState>();
                if let Ok(mut owned) = backend_state.owned.lock() {
                    let matching_process = owned
                        .as_ref()
                        .map(|current| {
                            current.pid == pid && current.instance_token == instance_token
                        })
                        .unwrap_or(false);
                    if matching_process {
                        owned.take();
                        backend_state
                            .set_message("Backend sidecar exited; it can be started again.");
                    }
                }
                break;
            }
        }
    });

    Ok(())
}

fn app_data_dir() -> Result<PathBuf, String> {
    if let Some(local_app_data) = std::env::var_os("LOCALAPPDATA") {
        return Ok(PathBuf::from(local_app_data).join(APP_DATA_DIR_NAME));
    }

    let current_dir = std::env::current_dir()
        .map_err(|error| format!("Failed to determine current directory: {error}"))?;

    Ok(current_dir.join(".video-course-cards"))
}

fn stop_owned_backend(state: &BackendState) -> Result<(), String> {
    let (pid, instance_token) = match owned_identity(state) {
        Some(identity) => identity,
        None => return Ok(()),
    };
    request_backend_quiesce(&instance_token)?;

    {
        let owned = state
            .owned
            .lock()
            .map_err(|_| "Backend process lock is poisoned.".to_string())?;
        let Some(process) = owned.as_ref() else {
            drop(owned);
            return wait_for_port_release();
        };
        if process.pid != pid || process.instance_token != instance_token {
            return Err("Backend ownership changed while preparing to stop.".to_string());
        }
        if let Err(error) = terminate_owned_process(process, terminate_process) {
            let message = format!(
                "Failed to stop backend sidecar; its owned child handle was retained for retry or app-exit cleanup: {error}"
            );
            state.set_message(message.clone());
            return Err(message);
        }
    }
    wait_for_port_release()?;
    let mut owned = state
        .owned
        .lock()
        .map_err(|_| "Backend process lock is poisoned.".to_string())?;
    let still_matches = owned
        .as_ref()
        .map(|process| process.pid == pid && process.instance_token == instance_token)
        .unwrap_or(false);
    if still_matches {
        owned.take();
    }
    Ok(())
}

fn wait_for_ready_status(
    state: &BackendState,
    mode: &str,
    expected_token: Option<&str>,
) -> Result<BackendStatus, String> {
    let started_at = Instant::now();

    while started_at.elapsed() < STARTUP_TIMEOUT {
        if is_backend_ready_for_token(expected_token) {
            let message = format!("Backend ready at {}.", backend_base_url());
            state.set_message(message.clone());
            return Ok(BackendStatus {
                ready: true,
                mode: mode.to_string(),
                message,
                application_id: APPLICATION_ID.to_string(),
                api_version: 1,
                identity_verified: true,
            });
        }

        std::thread::sleep(POLL_INTERVAL);
    }

    let message = format!(
        "Backend did not become ready within {} seconds.",
        STARTUP_TIMEOUT.as_secs()
    );
    state.set_message(message.clone());

    Err(message)
}

fn has_owned_backend(state: &BackendState) -> bool {
    owned_identity(state).is_some()
}

fn owned_identity(state: &BackendState) -> Option<(u32, String)> {
    state.owned.lock().ok().and_then(|owned| {
        owned
            .as_ref()
            .map(|process| (process.pid, process.instance_token.clone()))
    })
}

fn backend_base_url() -> String {
    format!("http://{}:{}", BACKEND_HOST, BACKEND_PORT)
}

fn is_backend_ready_for_token(expected_token: Option<&str>) -> bool {
    backend_health_response()
        .map(|response| has_expected_health_identity(&response, expected_token))
        .unwrap_or(false)
}

fn has_expected_health_identity(response: &str, expected_token: Option<&str>) -> bool {
    if http_status(response) != Some(200) {
        return false;
    }
    let Some(body) = http_body(response) else {
        return false;
    };
    let Ok(payload) = serde_json::from_str::<HealthPayload>(body) else {
        return false;
    };
    let contract_matches = payload.status == "ok"
        && payload.application_id == APPLICATION_ID
        && payload.api_version == 1;
    contract_matches
        && expected_token
            .map(|token| payload.instance_token.as_deref() == Some(token))
            .unwrap_or(true)
}

fn backend_health_response() -> Option<String> {
    send_http_request("GET", "/health", &[], HEALTH_TIMEOUT).ok()
}

fn send_http_request(
    method: &str,
    path: &str,
    headers: &[(&str, &str)],
    timeout: Duration,
) -> Result<String, String> {
    let address = SocketAddr::from(([127, 0, 0, 1], BACKEND_PORT));
    let mut stream = TcpStream::connect_timeout(&address, timeout)
        .map_err(|error| format!("Failed to connect to backend: {error}"))?;

    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));

    let mut request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {}:{}\r\n",
        BACKEND_HOST, BACKEND_PORT,
    );
    for (name, value) in headers {
        request.push_str(name);
        request.push_str(": ");
        request.push_str(value);
        request.push_str("\r\n");
    }
    request.push_str("Content-Length: 0\r\nConnection: close\r\n\r\n");

    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("Failed to write backend request: {error}"))?;

    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| format!("Failed to read backend response: {error}"))?;
    Ok(response)
}

fn http_status(response: &str) -> Option<u16> {
    response
        .lines()
        .next()?
        .split_whitespace()
        .nth(1)?
        .parse()
        .ok()
}

fn http_body(response: &str) -> Option<&str> {
    response.split_once("\r\n\r\n").map(|(_, body)| body.trim())
}

fn is_backend_port_reachable() -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], BACKEND_PORT));
    TcpStream::connect_timeout(&address, HEALTH_TIMEOUT).is_ok()
}

fn unrelated_port_message() -> String {
    format!(
        "Port {BACKEND_PORT} is in use by another service. Video Course Cards will not stop or replace an unowned process."
    )
}

fn request_backend_quiesce(instance_token: &str) -> Result<(), String> {
    if !is_backend_ready_for_token(Some(instance_token)) {
        return Err("Owned backend identity could not be verified before shutdown.".to_string());
    }
    let response = send_http_request(
        "POST",
        "/runtime/quiesce",
        &[(INSTANCE_TOKEN_HEADER, instance_token)],
        SHUTDOWN_TIMEOUT,
    )?;
    if http_status(&response) != Some(200) {
        return Err(format!(
            "Backend rejected safe shutdown with HTTP status {}.",
            http_status(&response)
                .map(|status| status.to_string())
                .unwrap_or_else(|| "unknown".to_string())
        ));
    }
    let body = http_body(&response)
        .ok_or_else(|| "Backend returned no safe-shutdown result.".to_string())?;
    let payload: QuiescePayload = serde_json::from_str(body)
        .map_err(|error| format!("Invalid safe-shutdown response: {error}"))?;
    if payload.status != "quiesced" || payload.instance_token != instance_token {
        return Err("Backend did not confirm a matching quiesced instance.".to_string());
    }
    Ok(())
}

fn wait_for_port_release() -> Result<(), String> {
    let started_at = Instant::now();
    while started_at.elapsed() < SHUTDOWN_TIMEOUT {
        if !is_backend_port_reachable() {
            return Ok(());
        }
        std::thread::sleep(POLL_INTERVAL);
    }
    Err(format!(
        "Owned backend did not release port {BACKEND_PORT} within {} seconds.",
        SHUTDOWN_TIMEOUT.as_secs()
    ))
}

fn terminate_owned_process<C>(
    process: &OwnedBackend<C>,
    terminate: impl FnOnce(u32) -> Result<(), String>,
) -> Result<(), String> {
    if process.child.is_none() {
        return Err(
            "The owned backend process handle is unavailable; no unknown process was stopped."
                .to_string(),
        );
    }
    terminate(process.pid)
}

#[cfg(windows)]
fn terminate_process(pid: u32) -> Result<(), String> {
    use windows_sys::Win32::{
        Foundation::{CloseHandle, GetLastError},
        System::Threading::{OpenProcess, TerminateProcess, PROCESS_TERMINATE},
    };

    unsafe {
        let handle = OpenProcess(PROCESS_TERMINATE, 0, pid);
        if handle.is_null() {
            return Err(format!(
                "OpenProcess({pid}) failed with Windows error {}.",
                GetLastError()
            ));
        }
        let terminated = TerminateProcess(handle, 1);
        let error = if terminated == 0 {
            Some(GetLastError())
        } else {
            None
        };
        CloseHandle(handle);
        match error {
            Some(code) => Err(format!(
                "TerminateProcess({pid}) failed with Windows error {code}."
            )),
            None => Ok(()),
        }
    }
}

#[cfg(unix)]
fn terminate_process(pid: u32) -> Result<(), String> {
    let result = unsafe { libc::kill(pid as libc::pid_t, libc::SIGKILL) };
    if result == 0 {
        Ok(())
    } else {
        Err(format!(
            "kill({pid}) failed: {}.",
            std::io::Error::last_os_error()
        ))
    }
}

#[cfg(not(any(unix, windows)))]
fn terminate_process(pid: u32) -> Result<(), String> {
    Err(format!(
        "Terminating owned process {pid} is unsupported on this platform."
    ))
}

#[cfg(test)]
mod tests {
    use super::{has_expected_health_identity, terminate_owned_process, OwnedBackend};

    #[test]
    fn accepts_the_video_course_cards_health_contract() {
        let response = concat!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n",
            "{\"status\":\"ok\",\"application_id\":\"video-course-cards\",",
            "\"api_version\":1,\"instance_token\":null}"
        );

        assert!(has_expected_health_identity(response, None));
    }

    #[test]
    fn requires_the_exact_owned_instance_token() {
        let response = concat!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n",
            "{\"status\":\"ok\",\"application_id\":\"video-course-cards\",",
            "\"api_version\":1,\"instance_token\":\"owned-token\"}"
        );

        assert!(has_expected_health_identity(response, Some("owned-token")));
        assert!(!has_expected_health_identity(
            response,
            Some("different-token")
        ));
    }

    #[test]
    fn rejects_an_unrelated_service_on_the_same_port() {
        let response = "HTTP/1.1 200 OK\r\n\r\n{\"status\":\"ok\",\"application_id\":\"other\"}";

        assert!(!has_expected_health_identity(response, None));
    }

    #[test]
    fn rejects_identity_text_outside_the_json_contract() {
        let response = concat!(
            "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n",
            "application_id=video-course-cards"
        );

        assert!(!has_expected_health_identity(response, None));
    }

    #[test]
    fn retains_the_owned_child_when_pid_termination_fails() {
        let process = OwnedBackend {
            child: Some(()),
            pid: 42,
            instance_token: "owned-token".to_string(),
        };

        let error = terminate_owned_process(&process, |_| {
            Err("simulated termination failure".to_string())
        })
        .expect_err("termination should fail");

        assert!(error.contains("simulated termination failure"));
        assert!(process.child.is_some());
    }

    #[test]
    fn refuses_pid_termination_without_a_real_owned_child() {
        let process = OwnedBackend::<()> {
            child: None,
            pid: 42,
            instance_token: "owned-token".to_string(),
        };
        let mut called = false;

        let error = terminate_owned_process(&process, |_| {
            called = true;
            Ok(())
        })
        .expect_err("missing ownership should fail closed");

        assert!(error.contains("handle is unavailable"));
        assert!(!called);
    }
}
