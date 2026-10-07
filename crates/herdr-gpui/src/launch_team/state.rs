//! A Launch team dialog's form and the host work behind it.
//!
//! Host work runs on a named thread and reports through a channel drained on
//! the window's tick. The team lookup stops when the dialog closes; a launch
//! already under way carries on, since stopping it halfway would strand a
//! worktree with some of its agents.

use super::{
    error::Error,
    job::{self, Launched, Request},
};
use crate::{search_input::SearchInput, teleport::Host};
use gpui::Entity;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
    mpsc,
};

/// Where a team launches, captured when the dialog opens.
#[derive(Debug, Clone)]
pub(crate) struct Origin {
    pub(crate) endpoint_id: String,
    pub(crate) endpoint_label: String,
    pub(crate) host: Host,
}

/// A repository open on the host, offered as a launch target.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Repo {
    /// Its Git common directory on the host.
    pub(crate) key: String,
    pub(crate) label: String,
    /// The branch a linked checkout's launch starts from, when the dialog
    /// was opened from that checkout.
    pub(crate) base: Option<String>,
}

/// The team files on the host, once listed.
#[derive(Debug)]
pub(crate) enum Teams {
    Loading,
    Listed(Vec<String>),
    Failed(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Stage {
    Compose,
    Launching,
}

/// Why the form cannot be submitted yet.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum NotReady {
    NoRepo,
    NoTeam,
    NoBranch,
    InvalidBranch,
    NoTask,
    Launching,
}

impl NotReady {
    pub(crate) fn message(self) -> &'static str {
        match self {
            Self::NoRepo => "Pick a repository or enter its path",
            Self::NoTeam => "Pick a team",
            Self::NoBranch => "Name the new branch",
            Self::InvalidBranch => "That is not a valid branch name",
            Self::NoTask => "Describe the task",
            Self::Launching => "Launching...",
        }
    }
}

/// What finished host work reports.
#[derive(Debug)]
pub(crate) enum Update {
    /// The launch failed; the dialog stays open on the form.
    Failed,
    Launched(Launched),
}

/// The dialog's text fields.
pub(crate) struct Fields {
    pub(crate) path: Entity<SearchInput>,
    pub(crate) branch: Entity<SearchInput>,
    pub(crate) task: Entity<SearchInput>,
}

enum Event {
    Teams(Result<Vec<String>, Error>),
    Launched(Result<Launched, Error>),
}

pub(crate) struct LaunchTeam {
    pub(crate) origin: Origin,
    pub(crate) stage: Stage,
    pub(crate) teams: Teams,
    pub(crate) selected: Option<usize>,
    pub(crate) repos: Vec<Repo>,
    pub(crate) repo: Option<usize>,
    /// A repository path typed on the host; used instead of `repo` when set.
    pub(crate) path: Entity<SearchInput>,
    pub(crate) branch: Entity<SearchInput>,
    pub(crate) task: Entity<SearchInput>,
    pub(crate) error: Option<Error>,
    sender: mpsc::Sender<Event>,
    events: mpsc::Receiver<Event>,
    /// Cancels the team lookup when the dialog closes.
    lookup: Arc<AtomicBool>,
    /// Cancels a launch only when the window goes away.
    work: Arc<AtomicBool>,
}

impl Drop for LaunchTeam {
    fn drop(&mut self) {
        self.lookup.store(true, Ordering::Release);
        self.work.store(true, Ordering::Release);
    }
}

fn spawn(work: impl FnOnce() + Send + 'static) {
    let spawned = std::thread::Builder::new()
        .name("herdr-launch-team".into())
        .spawn(work);
    if let Err(error) = spawned {
        tracing::warn!(%error, "could not start the launch team worker");
    }
}

impl LaunchTeam {
    /// Opens on the form at once, listing the host's teams meanwhile.
    pub(crate) fn start(
        origin: Origin,
        repos: Vec<Repo>,
        repo: Option<usize>,
        fields: Fields,
    ) -> Self {
        let Fields { path, branch, task } = fields;
        let (sender, events) = mpsc::channel();
        let launch = Self {
            origin,
            stage: Stage::Compose,
            teams: Teams::Loading,
            selected: None,
            repos,
            repo,
            path,
            branch,
            task,
            error: None,
            sender,
            events,
            lookup: Arc::new(AtomicBool::new(false)),
            work: Arc::new(AtomicBool::new(false)),
        };
        let host = launch.origin.host.clone();
        let sender = launch.sender.clone();
        let cancelled = launch.lookup.clone();
        spawn(move || {
            let _ = sender.send(Event::Teams(job::teams(&host, &cancelled)));
        });
        launch
    }

    pub(crate) fn launching(&self) -> bool {
        self.stage == Stage::Launching
    }

    /// Stop the team lookup, as when the dialog closes.
    pub(crate) fn stop_lookup(&self) {
        self.lookup.store(true, Ordering::Release);
    }

    pub(crate) fn team(&self) -> Option<&str> {
        match &self.teams {
            Teams::Listed(teams) => self.selected.and_then(|i| teams.get(i)).map(String::as_str),
            _ => None,
        }
    }

    /// The repository to launch into and its base: a typed path wins over
    /// the picked repository, and starts from that checkout's `HEAD`.
    pub(crate) fn target<'a>(&'a self, path: &'a str) -> Option<(&'a str, Option<&'a str>)> {
        let path = path.trim();
        if !path.is_empty() {
            return Some((path, None));
        }
        let repo = self.repos.get(self.repo?)?;
        Some((&repo.key, repo.base.as_deref()))
    }

    /// The first reason the form cannot launch, given its field values.
    pub(crate) fn not_ready(&self, path: &str, branch: &str, task: &str) -> Option<NotReady> {
        if self.launching() {
            return Some(NotReady::Launching);
        }
        if self.target(path).is_none() {
            return Some(NotReady::NoRepo);
        }
        if self.team().is_none() {
            return Some(NotReady::NoTeam);
        }
        if branch.trim().is_empty() {
            return Some(NotReady::NoBranch);
        }
        if crate::worktree::validate_branch(branch.trim()).is_err() {
            return Some(NotReady::InvalidBranch);
        }
        if task.trim().is_empty() {
            return Some(NotReady::NoTask);
        }
        None
    }

    /// Start the launch when the form is ready. Returns whether it started.
    pub(crate) fn launch(&mut self, path: &str, branch: &str, task: &str) -> bool {
        if self.not_ready(path, branch, task).is_some() {
            return false;
        }
        let (Some(team), Some((repo, base))) = (self.team(), self.target(path)) else {
            return false;
        };
        let request = Request {
            team: team.to_owned(),
            repo: repo.to_owned(),
            branch: branch.trim().to_owned(),
            task: task.trim().to_owned(),
            base: base.map(str::to_owned),
        };
        self.stage = Stage::Launching;
        self.error = None;
        let host = self.origin.host.clone();
        let sender = self.sender.clone();
        let cancelled = self.work.clone();
        spawn(move || {
            let _ = sender.send(Event::Launched(job::launch(&host, &request, &cancelled)));
        });
        true
    }

    /// Apply finished work: whether anything changed, and a finished launch.
    pub(crate) fn poll(&mut self) -> (bool, Option<Update>) {
        let mut changed = false;
        let mut update = None;
        while let Ok(event) = self.events.try_recv() {
            changed = true;
            match event {
                // A stopped lookup's late answer must not replace the form.
                Event::Teams(_) if self.lookup.load(Ordering::Acquire) => changed = false,
                Event::Teams(Ok(teams)) => {
                    // A single team is the obvious pick.
                    self.selected = (teams.len() == 1).then_some(0);
                    self.teams = Teams::Listed(teams);
                }
                Event::Teams(Err(error)) => {
                    tracing::warn!(%error, "launch team: listing teams");
                    self.teams = Teams::Failed(error.to_string());
                }
                Event::Launched(Ok(launched)) => update = Some(Update::Launched(launched)),
                Event::Launched(Err(error)) => {
                    tracing::warn!(%error, "launch team");
                    self.stage = Stage::Compose;
                    self.error = Some(error);
                    update = Some(Update::Failed);
                }
            }
        }
        (changed, update)
    }

    #[cfg(test)]
    pub(crate) fn teams_for_test(&mut self, teams: Vec<String>) {
        self.stop_lookup();
        self.selected = (teams.len() == 1).then_some(0);
        self.teams = Teams::Listed(teams);
    }

    #[cfg(test)]
    pub(crate) fn finish_for_test(&self, result: Result<Launched, Error>) {
        let _ = self.sender.send(Event::Launched(result));
    }
}
