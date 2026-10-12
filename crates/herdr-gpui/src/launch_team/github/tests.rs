#![allow(clippy::unwrap_used)]

use super::*;
use core::prelude::v1::test;
use gpui::TestAppContext;

fn repo(full_name: &str, description: &str, local: Option<&str>) -> GithubRepo {
    GithubRepo {
        full_name: full_name.into(),
        description: description.into(),
        private: false,
        local: local.map(Into::into),
    }
}

#[test]
fn replies_decode_to_the_value_or_the_reason() {
    let listing: Listing = decode(
        br#"progress
{"ok": true, "repos_dir": "/r", "repos": [{"full_name": "me/app", "description": "", "private": true, "local": "/r/app"}]}"#,
    )
    .unwrap();
    assert_eq!(listing.repos_dir, "/r");
    assert_eq!(listing.repos[0].local.as_deref(), Some("/r/app"));
    let ensured: Ensured = decode(br#"{"ok": true, "path": "/r/tool", "cloned": true}"#).unwrap();
    assert!(ensured.cloned);
    let refused =
        decode::<Ensured>(br#"{"ok": false, "error": "gh is not signed in"}"#).unwrap_err();
    assert_eq!(refused.to_string(), "gh is not signed in");
    assert!(matches!(
        decode::<Listing>(b"nonsense"),
        Err(Error::Decode(_))
    ));
}

#[test]
fn repository_names_are_quoted_for_the_shell() {
    let script = ensure_script("me/it's; rm -rf ~");
    assert!(script.contains(r"herdr-repos ensure 'me/it'\''s; rm -rf ~'"));
    assert!(list_script().ends_with("herdr-repos list\n"));
}

#[gpui::test]
fn search_matches_every_word_in_name_or_description(cx: &mut TestAppContext) {
    let search = cx.new(SearchInput::new);
    let picker = Picker::listed_for_test(
        search,
        Listing {
            repos_dir: "/r".into(),
            repos: vec![
                repo(
                    "me/herdr-gpui",
                    "Native Herdr client",
                    Some("/r/herdr-gpui"),
                ),
                repo("org/invoice", "Invoice assistant", None),
            ],
        },
    );
    let names = |query: &str| -> Vec<String> {
        picker
            .matches(query)
            .into_iter()
            .map(|r| r.full_name.clone())
            .collect()
    };
    assert_eq!(names(""), ["me/herdr-gpui", "org/invoice"]);
    assert_eq!(names("HERDR native"), ["me/herdr-gpui"]);
    assert_eq!(names("org assistant"), ["org/invoice"]);
    assert!(names("missing").is_empty());
}
