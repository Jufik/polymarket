mod chain_spec;
mod decoder;
mod filter;
mod network;

use pyo3::prelude::*;
use std::sync::Arc;
use tokio::sync::{mpsc, Mutex};

/// Wrapper to convert serde_json::Value into a Python object via pythonize.
///
/// Needed because `serde_json::Value` doesn't implement pyo3's `IntoPyObject`,
/// and `pyo3_async_runtimes::tokio::future_into_py` requires the return type to
/// implement it.
struct PyJsonValue(serde_json::Value);

impl<'py> IntoPyObject<'py> for PyJsonValue {
    type Target = PyAny;
    type Output = Bound<'py, PyAny>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        pythonize::pythonize(py, &self.0)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }
}

/// Polygon devp2p mempool monitor for Polymarket CTF Exchange trades.
///
/// Connects to Polygon peers, receives pending transaction gossip,
/// filters for fillOrder/fillOrders calls to CTF/NegRisk Exchange,
/// decodes calldata, and yields structured dicts to Python.
#[pyclass]
struct MempoolMonitor {
    listen_port: u16,
}

#[pymethods]
impl MempoolMonitor {
    #[new]
    #[pyo3(signature = (listen_port=30304, log_level="info,net::peers=trace,net::session=trace,net=debug,reth_eth_wire=debug,discv4=debug,disc::dns=debug,hickory_resolver=warn,hickory_proto=warn", log_file=None))]
    fn new(listen_port: u16, log_level: &str, log_file: Option<String>) -> Self {
        // Initialize tracing subscriber (once).
        // Default filter: TRACE for reth's net::peers (fill_outbound_slots dial decisions,
        // best_unconnected selection, fork ID checks) and net::session (handshake lifecycle).
        // DEBUG for net (NetworkManager connect/disconnect), reth_eth_wire (Status exchange),
        // discv4 (discovery v4), disc::dns (DNS discovery).
        // hickory_resolver/hickory_proto suppressed at WARN (DNS library noise).
        // Use log_level="trace" for maximum verbosity.
        match log_file.as_deref() {
            Some(path) => {
                let file = std::fs::File::create(path)
                    .unwrap_or_else(|e| panic!("Cannot create log file {path}: {e}"));
                let _ = tracing_subscriber::fmt()
                    .with_env_filter(log_level)
                    .with_ansi(false)
                    .with_writer(std::sync::Mutex::new(file))
                    .try_init();
            }
            None => {
                let _ = tracing_subscriber::fmt()
                    .with_env_filter(log_level)
                    .try_init();
            }
        }
        Self { listen_port }
    }

    /// Returns an async iterator of decoded pending trade dicts.
    ///
    /// Each dict contains: tx_hash, maker, taker, token_id,
    /// maker_amount, taker_amount, fee_rate_bps, side, expiration, seen_at.
    ///
    /// Also yields status dicts with _peers_active key.
    fn stream(&self) -> MempoolStream {
        let (tx, rx) = mpsc::channel(10_000);

        let port = self.listen_port;
        // Spawn the network runner on a dedicated tokio runtime in a background thread.
        // This thread owns the runtime for the lifetime of the stream.
        std::thread::Builder::new()
            .name("mempool-network".to_string())
            .spawn(move || {
                let rt = tokio::runtime::Runtime::new().unwrap();
                rt.block_on(async {
                    if let Err(e) = network::runner::run_network(port, tx).await {
                        tracing::error!("Network runner failed: {}", e);
                    }
                });
            })
            .expect("failed to spawn network thread");

        MempoolStream {
            rx: Arc::new(Mutex::new(rx)),
        }
    }
}

/// Async iterator that yields trade dicts from the Rust network runner.
///
/// Each call to `__anext__` returns a Python coroutine that awaits
/// the next value from the mpsc channel. This properly suspends the
/// Python event loop until data arrives (no busy-polling).
#[pyclass]
struct MempoolStream {
    rx: Arc<Mutex<mpsc::Receiver<serde_json::Value>>>,
}

#[pymethods]
impl MempoolStream {
    fn __aiter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    /// Returns a Python coroutine that awaits the next trade dict.
    fn __anext__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let rx = self.rx.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let mut guard = rx.lock().await;
            match guard.recv().await {
                Some(val) => Ok(PyJsonValue(val)),
                None => Err(pyo3::exceptions::PyStopAsyncIteration::new_err(
                    "Network runner disconnected",
                )),
            }
        })
    }
}

#[pymodule]
fn polymarket_mempool(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<MempoolMonitor>()?;
    m.add_class::<MempoolStream>()?;
    Ok(())
}
