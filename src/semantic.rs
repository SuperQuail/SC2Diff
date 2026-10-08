//! Semantic, order-independent diff for document components.
//!
//! The editor rewrites a component wholesale and sibling order carries no meaning, so a
//! text diff reports enormous churn for a one-field edit.  Every component is instead
//! reduced to an identity-keyed model (placed objects by Id, triggers by Id, catalog
//! entries by tag+id, localised text by key) and compared as maps.

use std::collections::BTreeMap;
use quick_xml::events::Event;
use quick_xml::Reader;

#[derive(Debug, Clone, PartialEq)]
pub enum Kind { Xml, Text, Galaxy, Version, Binary }

impl Default for Kind {
    fn default() -> Self { Kind::Binary }
}

pub fn classify(name: &str, data: &[u8]) -> Kind {
    let base = name.rsplit('\\').next().unwrap_or(name).rsplit('/').next().unwrap_or(name);
    if base.ends_with(".xml") || base.ends_with(".SC2Components") || base.ends_with(".SC2Layout")
        || base.ends_with(".SC2Style") || matches!(base, "Objects" | "Triggers" | "DocumentInfo") {
        return Kind::Xml;
    }
    if base.ends_with(".galaxy") { return Kind::Galaxy; }
    if base.ends_with(".version") { return Kind::Version; }
    if base.ends_with(".txt") { return Kind::Text; }
    if data.starts_with(b"<?xml") { return Kind::Xml; }
    if data.len() >= 4 && &data[0..4] == b"cdes" { return Kind::Version; }
    if !data.is_empty() && !data[..data.len().min(512)].contains(&0) {
        if std::str::from_utf8(&data[..data.len().min(512)]).is_ok() { return Kind::Text; }
    }
    Kind::Binary
}

#[derive(Debug, Clone, Default)]
pub struct Element {
    pub name: String,
    pub attrs: Vec<(String, String)>,
    pub text: String,
    pub children: Vec<Element>,
}

impl Element {
    fn attr(&self, key: &str) -> Option<&str> {
        self.attrs.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_str())
    }
}

pub fn parse_xml(text: &str) -> Option<Element> {
    fn build(e: &quick_xml::events::BytesStart) -> Element {
        let mut el = Element {
            name: String::from_utf8_lossy(e.name().as_ref()).to_string(),
            ..Default::default()
        };
        for a in e.attributes().flatten() {
            el.attrs.push((
                String::from_utf8_lossy(a.key.as_ref()).to_string(),
                String::from_utf8_lossy(&a.value).to_string(),
            ));
        }
        el
    }

    let mut reader = Reader::from_str(text);
    let mut buf = Vec::new();
    let mut stack: Vec<Element> = Vec::new();
    let mut root: Option<Element> = None;
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => stack.push(build(e)),
            Ok(Event::Empty(ref e)) => {
                let el = build(e);
                match stack.last_mut() {
                    Some(parent) => parent.children.push(el),
                    None => root = Some(el),
                }
            }
            Ok(Event::End(_)) => {
                if let Some(el) = stack.pop() {
                    match stack.last_mut() {
                        Some(parent) => parent.children.push(el),
                        None => root = Some(el),
                    }
                }
            }
            Ok(Event::Text(ref t)) => {
                if let Some(top) = stack.last_mut() {
                    let s = t.unescape().unwrap_or_default().to_string();
                    if !s.trim().is_empty() { top.text.push_str(s.trim()); }
                }
            }
            Ok(Event::Eof) | Err(_) => break,
            _ => {},
        }
        buf.clear();
    }
    while let Some(el) = stack.pop() {
        match stack.last_mut() {
            Some(parent) => parent.children.push(el),
            None => root = Some(el),
        }
    }
    root
}

const IDENTITY_ATTRS: [&str; 10] = ["Index", "index", "Id", "id", "Type", "type", "Name", "name", "Link", "link"];

fn identity(el: &Element) -> Option<String> {
    for a in IDENTITY_ATTRS {
        if let Some(v) = el.attr(a) {
            return Some(format!("{}[{}={}]", el.name, a, v));
        }
    }
    None
}

fn label(el: &Element, ambiguity: usize, ordinal: usize) -> String {
    match identity(el) {
        Some(l) => l,
        None => if ambiguity == 1 { el.name.clone() } else { format!("{}[{}]", el.name, ordinal) },
    }
}

fn flatten(el: &Element, path: &str, out: &mut BTreeMap<String, String>) {
    if !el.text.is_empty() { out.insert(format!("{path}/#text"), el.text.clone()); }
    for (k, v) in &el.attrs { out.insert(format!("{path}/@{k}"), v.clone()); }
    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
    for c in &el.children { *counts.entry(c.name.as_str()).or_insert(0) += 1; }
    let mut seen: BTreeMap<&str, usize> = BTreeMap::new();
    for c in &el.children {
        let ord = *seen.entry(c.name.as_str()).or_insert(0);
        *seen.get_mut(c.name.as_str()).unwrap() += 1;
        let child_label = label(c, counts[c.name.as_str()], ord);
        let child_path = if path.is_empty() { child_label } else { format!("{path}/{child_label}") };
        flatten(c, &child_path, out);
    }
}

#[derive(Debug, Clone, Default)]
pub struct Model {
    pub kind: Kind,
    pub root: String,
    pub root_attrs: String,
    pub entities: BTreeMap<String, BTreeMap<String, String>>,
    pub lines: Vec<String>,
    pub size: usize,
    pub digest: String,
}

pub fn canonical(name: &str, data: &[u8]) -> Model {
    let kind = classify(name, data);
    let mut m = Model { kind: kind.clone(), size: data.len(), ..Default::default() };
    match kind {
        Kind::Xml => {
            let text = String::from_utf8_lossy(data);
            match parse_xml(&text) {
                Some(root) => {
                    m.root = root.name.clone();
                    m.root_attrs = root.attrs.iter().map(|(k, v)| format!("{k}=\"{v}\"")).collect::<Vec<_>>().join(" ");
                    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
                    for c in &root.children { *counts.entry(c.name.as_str()).or_insert(0) += 1; }
                    let mut seen: BTreeMap<&str, usize> = BTreeMap::new();
                    for c in &root.children {
                        let ord = *seen.entry(c.name.as_str()).or_insert(0);
                        *seen.get_mut(c.name.as_str()).unwrap() += 1;
                        let mut key = label(c, counts[c.name.as_str()], ord);
                        if m.entities.contains_key(&key) {
                            let mut n = 2;
                            while m.entities.contains_key(&format!("{key}#{n}")) { n += 1; }
                            key = format!("{key}#{n}");
                        }
                        let mut fields = BTreeMap::new();
                        flatten(c, "", &mut fields);
                        m.entities.insert(key, fields);
                    }
                }
                None => { m.lines = lines_of(data); }
            }
        }
        Kind::Version => {
            let mut e = BTreeMap::new();
            if data.len() >= 36 {
                e.insert("bytes".into(), data.len().to_string());
                e.insert("build".into(), u32::from_le_bytes([data[8], data[9], data[10], data[11]]).to_string());
                e.insert("timestamp".into(), u32::from_le_bytes([data[32], data[33], data[34], data[35]]).to_string());
            }
            m.entities.insert("version".into(), e);
        }
        Kind::Binary => { m.digest = hex(&sha1(data)); }
        _ => { m.lines = lines_of(data); }
    }
    m
}

fn lines_of(data: &[u8]) -> Vec<String> {
    String::from_utf8_lossy(data).lines().map(|l| l.trim().to_string()).filter(|l| !l.is_empty()).collect()
}

pub fn sha1(data: &[u8]) -> [u8; 20] {
    use sha1::{Digest, Sha1};
    let mut h = Sha1::new();
    h.update(data);
    let out = h.finalize();
    let mut a = [0u8; 20];
    a.copy_from_slice(&out);
    a
}

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Change {
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub changed: BTreeMap<String, Vec<String>>,
    pub root: String,
    pub lines_added: usize,
    pub lines_removed: usize,
    pub binary_changed: bool,
    pub kind: String,
}

impl Change {
    pub fn is_empty(&self) -> bool {
        self.added.is_empty() && self.removed.is_empty() && self.changed.is_empty()
            && !self.binary_changed && self.lines_added == 0 && self.lines_removed == 0
    }

    pub fn summary(&self, _name: &str) -> String {
        if self.binary_changed { return format!("binary: {}", self.kind); }
        if self.lines_added > 0 || self.lines_removed > 0 {
            return format!("{}: +{} -{} lines", self.kind, self.lines_added, self.lines_removed);
        }
        let mut bits = Vec::new();
        if !self.added.is_empty() { bits.push(format!("+{} entities", self.added.len())); }
        if !self.removed.is_empty() { bits.push(format!("-{} entities", self.removed.len())); }
        if !self.changed.is_empty() { bits.push(format!("~{} entities", self.changed.len())); }
        if self.root.is_empty() && bits.is_empty() { return "no change".to_string(); }
        format!("xml({}): {}", self.root, if bits.is_empty() { "no change".to_string() } else { bits.join(", ") })
    }
}

pub fn diff(a: &Model, b: &Model) -> Change {
    let mut c = Change { kind: format!("{:?}", b.kind).to_lowercase(), ..Default::default() };
    match b.kind {
        Kind::Binary => {
            c.binary_changed = a.digest != b.digest;
            c.kind = format!("{} -> {} bytes", a.size, b.size);
        }
        Kind::Xml if !b.entities.is_empty() => {
            c.root = b.root.clone();
            for k in b.entities.keys() {
                if !a.entities.contains_key(k) { c.added.push(k.clone()); }
            }
            for k in a.entities.keys() {
                if !b.entities.contains_key(k) { c.removed.push(k.clone()); }
            }
            for (k, bf) in &b.entities {
                if let Some(af) = a.entities.get(k) {
                    let mut fields = Vec::new();
                    for (fk, bv) in bf {
                        match af.get(fk) {
                            None => fields.push(format!("      + {fk} = {bv}")),
                            Some(av) if av != bv => fields.push(format!("      {fk}: {av} -> {bv}")),
                            _ => {}
                        }
                    }
                    for (fk, av) in af {
                        if !bf.contains_key(fk) { fields.push(format!("      - {fk} = {av}")); }
                    }
                    if !fields.is_empty() { c.changed.insert(k.clone(), fields); }
                }
            }
        }
        _ => {
            c.lines_added = b.lines.iter().filter(|l| !a.lines.contains(l)).count();
            c.lines_removed = a.lines.iter().filter(|l| !b.lines.contains(l)).count();
        }
    }
    c
}

pub fn render(name: &str, c: &Change) -> String {
    let mut out = vec![format!("### {name}")];
    for k in &c.removed { out.push(format!("  - {k}")); }
    for k in &c.added { out.push(format!("  + {k}")); }
    for (k, fields) in &c.changed {
        out.push(format!("  ~ {k}"));
        for f in fields.iter().take(8) { out.push(f.clone()); }
        if fields.len() > 8 { out.push(format!("      ... {} more fields", fields.len() - 8)); }
    }
    out.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_one_field_edit_reports_one_field() {
        let a = canonical("Objects", b"<?xml version=\"1.0\"?><PlacedObjects Version=\"27\"><ObjectUnit Id=\"1\" UnitType=\"Marine\"/><ObjectUnit Id=\"2\" UnitType=\"Medivac\"/></PlacedObjects>");
        let b = canonical("Objects", b"<?xml version=\"1.0\"?><PlacedObjects Version=\"27\"><ObjectUnit Id=\"1\" UnitType=\"Marine\"/><ObjectUnit Id=\"2\" UnitType=\"Viking\"/></PlacedObjects>");
        let d = diff(&a, &b);
        assert_eq!(d.added.len(), 0);
        assert_eq!(d.removed.len(), 0);
        assert_eq!(d.changed.len(), 1, "only one entity should differ: {d:?}");
        assert!(d.changed.contains_key("ObjectUnit[Id=2]"));
    }

    #[test]
    fn reordered_siblings_are_not_a_change() {
        let a = canonical("Objects", b"<PlacedObjects><ObjectUnit Id=\"1\"/><ObjectUnit Id=\"2\"/></PlacedObjects>");
        let b = canonical("Objects", b"<PlacedObjects><ObjectUnit Id=\"2\"/><ObjectUnit Id=\"1\"/></PlacedObjects>");
        assert!(diff(&a, &b).is_empty());
    }

    #[test]
    fn entity_ids_are_stable_across_insertion() {
        let a = canonical("Objects", b"<PlacedObjects><ObjectUnit Id=\"2\"/></PlacedObjects>");
        let b = canonical("Objects", b"<PlacedObjects><ObjectUnit Id=\"1\"/><ObjectUnit Id=\"2\"/></PlacedObjects>");
        let d = diff(&a, &b);
        assert_eq!(d.added, vec!["ObjectUnit[Id=1]".to_string()]);
        assert!(d.removed.is_empty() && d.changed.is_empty());
    }
}
