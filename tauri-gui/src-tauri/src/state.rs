use std::process::Child;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};

#[derive(Clone)]
pub(crate) struct ActiveRun {
    pub(crate) child: Arc<Mutex<Option<Child>>>,
    pub(crate) cancelled: Arc<AtomicBool>,
}

#[derive(Default)]
pub(crate) struct AppState {
    pub(crate) run: Arc<Mutex<Option<ActiveRun>>>,
}
