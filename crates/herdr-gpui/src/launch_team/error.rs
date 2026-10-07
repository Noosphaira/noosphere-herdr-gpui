//! Launch failures keep their typed cause until the dialog shows them.

#[derive(Debug, thiserror::Error)]
pub(crate) enum Error {
    #[error("Could not run herdr-launch: {0}")]
    Script(#[source] herdr_client::Error),
    #[error("herdr-launch gave unexpected output")]
    Decode(#[source] serde_json::Error),
    /// `herdr-launch` ran and reported which of its steps failed.
    #[error("{step} failed: {message}")]
    Failed {
        step: String,
        message: String,
        output: String,
    },
    #[error("Launch cancelled")]
    Cancelled,
}

impl From<herdr_client::Error> for Error {
    fn from(source: herdr_client::Error) -> Self {
        match source {
            herdr_client::Error::ScriptCancelled => Self::Cancelled,
            source => Self::Script(source),
        }
    }
}

impl From<serde_json::Error> for Error {
    fn from(source: serde_json::Error) -> Self {
        Self::Decode(source)
    }
}

impl Error {
    /// The failing command's own output, shown under the message.
    pub(crate) fn output(&self) -> Option<&str> {
        match self {
            Self::Failed { output, .. } if !output.trim().is_empty() => Some(output),
            _ => None,
        }
    }
}

pub(crate) type Result<T, E = Error> = std::result::Result<T, E>;
