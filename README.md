  # 🐀 Rat Studio

Rat Studio is a little IDE and compiler I’m making for my own programming language, **Rat**.

The main idea is pretty simple: make programming feel fun and different while still giving people the tools they need to actually write and run code.

Right now, Rat Studio is in **Demo v0.0**, so it's still an early build and things will definitely change.

## What is Rat?

Rat is a custom programming language with its own keywords and syntax.

For example:

```rat
R name = "SernTheRat"

printR(name)
```

Some of the current keywords are:

```text
R
plague
printR
ifR
elifR
elseR
whileR
infect
cheese
trueR
falseR
nullR
Rnot
andT
orR
fang
bite
strike
call
coil
shed
```

I'm still working on the language, so the syntax isn't final yet.

## What does Rat Studio have?

Rat Studio is meant to be more than just a compiler.

It currently has:

* A Rat code editor
* Rat file/project support
* Run and compile tools
* Syntax highlighting
* Code folding
* Rat Basics tutorials
* Custom UI colors
* Custom Rat keyword colors
* Saved settings
* An AI assistant
* Ollama support for local AI
* A classic Windows-inspired interface

## Rat Basics

There is a built-in tutorial for learning Rat from the beginning.

It covers things like:

* Variables
* Printing
* Numbers
* Strings
* Booleans
* Conditions
* Loops
* Functions
* Operators
* Basic programming concepts

The goal is to let someone open Rat Studio and start learning without needing a separate tutorial.

## AI

Rat Studio can use Ollama for local AI.

The current setup uses:

```text
llama3.2
```

Ollama needs to be installed and running for the local AI features to work.

## Customization

One thing I wanted Rat Studio to have from the start was customization.

You can change things like:

* UI colors
* Rat colors
* Console colors
* Keyword colors

The settings are saved so your setup stays the same when you reopen Rat Studio.

## Demo v0.0

This is an early demo, not a finished programming language.

I'm mainly using this version to test:

* The Rat language
* The compiler
* The IDE
* The UI
* The AI features
* New ideas

So there may be bugs, unfinished features, or things that change later.

## Project Structure

```text
Rat Studio/
├── rat_compiler.py
├── rat_studio_settings.json
├── rat_studio_state.json
├── projects/
├── README.md
└── version_info.txt
```

## Reporting Bugs

Found something broken?

Open a GitHub issue and tell me:

* What you were doing
* What you expected
* What happened instead
* Any error message you got
* How to reproduce it

Screenshots are always helpful too.

## What's Next?

I'm planning to keep expanding Rat with more language features and improving the IDE as I go.

New keywords, better tooling, more tutorials, better AI features, and a more complete compiler are all part of the plan.

## 🐀

Rat Studio is still a work in progress, but this is the beginning of it.

**Made by SernTheRat.**
