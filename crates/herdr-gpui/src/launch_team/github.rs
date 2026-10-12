//! Picking the repository from GitHub: list your repositories, and use the
//! checkout in the repositories folder, cloning it there first if needed.
//!
//! Both run as host scripts on the dialog's host, through `herdr-repos` and
//! the GitHub CLI signed in there (`contrib/local-agents`), so the folder and
//! the clone are on the machine the team launches on.

use crate::{HerdrWindow, search_input::SearchInput, teleport::Host};
use gpui::{prelude::*, *};
use herdr_client::shell_quote;
use serde::Deserialize;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
    mpsc,
};

/// Rows shown at once; the search narrows the rest.
const SHOWN: usize = 40;

const NOT_INSTALLED: &str = r#"if ! command -v herdr-repos >/dev/null 2>&1; then
  printf '%s\n' '{"ok":false,"error":"herdr-repos is not installed on this host; run contrib/local-agents/install.sh"}'
  exit 0
fi
"#;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct GithubRepo {
    pub(crate) full_name: String,
    #[serde(default)]
    pub(crate) description: String,
    #[serde(default)]
    pub(crate) private: bool,
    /// The checkout in the repositories folder, if there is one.
    #[serde(default)]
    pub(crate) local: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct Listing {
    pub(crate) repos_dir: String,
    pub(crate) repos: Vec<GithubRepo>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct Ensured {
    pub(crate) path: String,
    pub(crate) cloned: bool,
}

#[derive(Debug, thiserror::Error)]
pub(crate) enum Error {
    #[error("Could not run herdr-repos: {0}")]
    Script(#[source] herdr_client::Error),
    #[error("herdr-repos gave unexpected output")]
    Decode(#[source] serde_json::Error),
    /// herdr-repos explained what went wrong.
    #[error("{0}")]
    Refused(String),
}

#[derive(Deserialize)]
struct Reply<T> {
    ok: bool,
    #[serde(default)]
    error: String,
    #[serde(flatten)]
    value: Option<T>,
}

/// The last non-empty line is the reply; clone progress goes to stderr.
pub(crate) fn decode<T: serde::de::DeserializeOwned>(output: &[u8]) -> Result<T, Error> {
    let text = String::from_utf8_lossy(output);
    let last = text
        .lines()
        .rev()
        .find(|l| !l.trim().is_empty())
        .unwrap_or("");
    let reply: Reply<T> = serde_json::from_str(last).map_err(Error::Decode)?;
    match (reply.ok, reply.value) {
        (true, Some(value)) => Ok(value),
        _ => Err(Error::Refused(if reply.error.is_empty() {
            "herdr-repos failed".into()
        } else {
            reply.error
        })),
    }
}

pub(crate) fn list_script() -> String {
    format!("{NOT_INSTALLED}herdr-repos list\n")
}

pub(crate) fn ensure_script(full_name: &str) -> String {
    format!(
        "{NOT_INSTALLED}herdr-repos ensure {}\n",
        shell_quote(full_name)
    )
}

fn run<T: serde::de::DeserializeOwned>(
    host: &Host,
    script: &str,
    cancelled: &AtomicBool,
) -> Result<T, Error> {
    decode(&host.capture(script, cancelled).map_err(Error::Script)?)
}

pub(crate) enum State {
    Loading,
    Listed(Listing),
    Failed(String),
}

enum Event {
    Listed(Result<Listing, Error>),
    Ensured(Result<Ensured, Error>),
}

pub(crate) struct Picker {
    pub(crate) state: State,
    pub(crate) search: Entity<SearchInput>,
    /// The repository being cloned, while it is.
    pub(crate) ensuring: Option<String>,
    pub(crate) error: Option<String>,
    sender: mpsc::Sender<Event>,
    events: mpsc::Receiver<Event>,
    /// Stops the listing and any clone when the picker or dialog closes.
    cancelled: Arc<AtomicBool>,
}

impl Drop for Picker {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Release);
    }
}

fn spawn(work: impl FnOnce() + Send + 'static) {
    if let Err(error) = std::thread::Builder::new()
        .name("herdr-github-repos".into())
        .spawn(work)
    {
        tracing::warn!(%error, "could not start the GitHub repository worker");
    }
}

impl Picker {
    pub(crate) fn open(host: Host, search: Entity<SearchInput>) -> Self {
        let (sender, events) = mpsc::channel();
        let cancelled = Arc::new(AtomicBool::new(false));
        let (tx, flag) = (sender.clone(), cancelled.clone());
        spawn(move || {
            let _ = tx.send(Event::Listed(run(&host, &list_script(), &flag)));
        });
        Self {
            state: State::Loading,
            search,
            ensuring: None,
            error: None,
            sender,
            events,
            cancelled,
        }
    }

    /// Find or clone `full_name`; the result arrives through `poll`.
    pub(crate) fn ensure(&mut self, host: Host, full_name: &str) {
        if self.ensuring.is_some() {
            return;
        }
        self.ensuring = Some(full_name.to_owned());
        self.error = None;
        let (tx, flag, name) = (
            self.sender.clone(),
            self.cancelled.clone(),
            full_name.to_owned(),
        );
        spawn(move || {
            let _ = tx.send(Event::Ensured(run(&host, &ensure_script(&name), &flag)));
        });
    }

    /// Apply finished work: whether anything changed, and a ready checkout.
    pub(crate) fn poll(&mut self) -> (bool, Option<Ensured>) {
        let mut changed = false;
        let mut ready = None;
        while let Ok(event) = self.events.try_recv() {
            changed = true;
            match event {
                Event::Listed(Ok(listing)) => self.state = State::Listed(listing),
                Event::Listed(Err(error)) => self.state = State::Failed(error.to_string()),
                Event::Ensured(Ok(ensured)) => {
                    self.ensuring = None;
                    ready = Some(ensured);
                }
                Event::Ensured(Err(error)) => {
                    self.ensuring = None;
                    self.error = Some(error.to_string());
                }
            }
        }
        (changed, ready)
    }

    /// Repositories matching every word of `query`, most recently pushed first.
    pub(crate) fn matches<'a>(&'a self, query: &str) -> Vec<&'a GithubRepo> {
        let State::Listed(listing) = &self.state else {
            return Vec::new();
        };
        let words: Vec<String> = query.split_whitespace().map(str::to_lowercase).collect();
        listing
            .repos
            .iter()
            .filter(|repo| {
                let haystack = format!("{} {}", repo.full_name, repo.description).to_lowercase();
                words.iter().all(|word| haystack.contains(word))
            })
            .collect()
    }

    #[cfg(test)]
    pub(crate) fn listed_for_test(search: Entity<SearchInput>, listing: Listing) -> Self {
        let (sender, events) = mpsc::channel();
        Self {
            state: State::Listed(listing),
            search,
            ensuring: None,
            error: None,
            sender,
            events,
            cancelled: Arc::new(AtomicBool::new(true)),
        }
    }
}

impl HerdrWindow {
    pub(crate) fn toggle_github_picker(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(launch) = &mut self.launch_team else {
            return;
        };
        if launch.github.take().is_some() || launch.launching() {
            cx.notify();
            return;
        }
        let search = cx.new(SearchInput::new);
        search.update(cx, |input, cx| {
            input.set_placeholder("Search your GitHub repositories", cx);
            input.set_appearance(self.config.ui.clone(), self.theme.clone(), cx);
        });
        window.focus(&search.read(cx).focus.clone(), cx);
        let host = launch.origin.host.clone();
        launch.github = Some(Picker::open(host, search));
        cx.notify();
    }

    /// Use `repo`: its checkout at once if there is one, otherwise clone it.
    fn pick_github_repo(&mut self, repo: &GithubRepo, cx: &mut Context<Self>) {
        let Some(launch) = &mut self.launch_team else {
            return;
        };
        if let Some(path) = &repo.local {
            let path = path.clone();
            launch.github = None;
            launch
                .path
                .update(cx, |input, cx| input.set_text(&path, cx));
        } else if let Some(picker) = &mut launch.github {
            picker.ensure(launch.origin.host.clone(), &repo.full_name);
        }
        cx.notify();
    }

    /// Called from the dialog's poll: a cloned or found checkout becomes the path.
    pub(crate) fn poll_github_picker(&mut self, cx: &mut Context<Self>) -> bool {
        let Some(launch) = &mut self.launch_team else {
            return false;
        };
        let Some(picker) = &mut launch.github else {
            return false;
        };
        let (changed, ready) = picker.poll();
        if let Some(ensured) = ready {
            launch.github = None;
            launch
                .path
                .update(cx, |input, cx| input.set_text(&ensured.path, cx));
        }
        changed
    }

    pub(crate) fn render_github_picker(&self, cx: &mut Context<Self>) -> Option<Div> {
        let picker = self.launch_team.as_ref()?.github.as_ref()?;
        let theme = &self.theme;
        let muted = rgb(theme.muted);
        let danger = crate::menu::danger(theme);
        let line = |text: String| div().min_w_0().child(text);
        let mut panel = div()
            .debug_selector(|| "launch-team-github".into())
            .flex()
            .flex_col()
            .gap(px(6.))
            .p(px(8.))
            .rounded(px(crate::config::corners::CONTROL))
            .border_1()
            .border_color(rgb(theme.active))
            .child(picker.search.clone());
        match &picker.state {
            State::Loading => {
                panel = panel.child(line("Loading your repositories...".into()).text_color(muted));
            }
            State::Failed(error) => {
                panel = panel.child(
                    line(error.clone())
                        .debug_selector(|| "launch-team-github-error".into())
                        .text_color(danger),
                );
            }
            State::Listed(listing) => {
                let query = picker.search.read(cx).text().to_owned();
                let matches = picker.matches(&query);
                let total = matches.len();
                let mut rows = div()
                    .id("launch-team-github-list")
                    .flex()
                    .flex_col()
                    .max_h(px(self.config.ui.line_height() * 9.))
                    .overflow_y_scroll();
                for (index, repo) in matches.into_iter().take(SHOWN).enumerate() {
                    let pick = repo.clone();
                    let busy = picker.ensuring.as_deref() == Some(repo.full_name.as_str());
                    let badge = if busy {
                        Some("cloning...")
                    } else if repo.local.is_some() {
                        Some("cloned")
                    } else if repo.private {
                        Some("private")
                    } else {
                        None
                    };
                    rows = rows.child(
                        div()
                            .id(("launch-team-github-repo", index))
                            .debug_selector(move || format!("launch-team-github-repo-{index}"))
                            .flex()
                            .items_center()
                            .gap(px(8.))
                            .px(px(6.))
                            .py(px(3.))
                            .rounded(px(crate::config::corners::CONTROL))
                            .when(picker.ensuring.is_none(), |row| {
                                row.cursor_pointer().hover(|row| row.bg(rgb(theme.active)))
                            })
                            .child(
                                div()
                                    .flex_1()
                                    .min_w_0()
                                    .truncate()
                                    .child(repo.full_name.clone()),
                            )
                            .children(
                                badge.map(|badge| div().flex_none().text_color(muted).child(badge)),
                            )
                            .on_click(cx.listener(move |this, _, _, cx| {
                                cx.stop_propagation();
                                this.pick_github_repo(&pick, cx);
                            })),
                    );
                }
                let note = match total {
                    0 => "No repository matches.".to_owned(),
                    n if n > SHOWN => format!("{SHOWN} of {n} shown; search to narrow."),
                    _ => format!("Not cloned yet goes to {}.", listing.repos_dir),
                };
                panel = panel.child(rows).child(line(note).text_color(muted));
            }
        }
        if let Some(name) = &picker.ensuring {
            panel = panel.child(line(format!("Cloning {name}...")).text_color(muted));
        }
        if let Some(error) = &picker.error {
            panel = panel.child(
                line(error.clone())
                    .debug_selector(|| "launch-team-github-error".into())
                    .text_color(danger),
            );
        }
        Some(panel)
    }
}

#[cfg(test)]
mod tests;
