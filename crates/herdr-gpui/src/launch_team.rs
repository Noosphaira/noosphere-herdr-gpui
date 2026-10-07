//! Launch team: start one sandboxed agent per role of a team on a new
//! worktree, all from one dialog.
//!
//! The work is done by the `herdr-launch` script on the workspace's host
//! (`contrib/local-agents`), which owns team files, folder grants, and the
//! bubblewrap sandbox. The GUI only collects the team, branch, and task, runs
//! that script as a background host script (locally or over SSH, as Fan out
//! and Teleport do), and follows the workspace it created.

mod error;
mod job;
mod state;
mod ui;

pub(crate) use state::{LaunchTeam, Origin};

#[cfg(test)]
mod tests;
