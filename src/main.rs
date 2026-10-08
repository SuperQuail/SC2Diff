use clap::Parser;

use sc2diff::cli::{run, Cli};

fn main() {
    let cli = Cli::parse();
    match run(cli) {
        Ok(code) => std::process::exit(code),
        Err(e) => {
            eprintln!("fatal: {e}");
            std::process::exit(128);
        }
    }
}
