; Elixir symbol extraction

(call
  target: (identifier) @_keyword
  (arguments
    (alias) @name.definition.class)
  (#match? @_keyword "^(defmodule)$")) @definition.class

(call
  target: (identifier) @_keyword
  (arguments
    (identifier) @name.definition.function)
  (#match? @_keyword "^(def|defp|defmacro|defmacrop|defguard|defguardp)$")) @definition.function

(call
  target: (identifier) @_keyword
  (arguments
    (call
      target: (identifier) @name.definition.function))
  (#match? @_keyword "^(def|defp|defmacro|defmacrop|defguard|defguardp)$")) @definition.function

(call
  target: (identifier) @name.reference.call) @reference.call

(call
  target: (dot
    right: (identifier) @name.reference.call)) @reference.call

(call
  target: (identifier) @_keyword
  (arguments
    (alias) @name.reference.import)
  (#match? @_keyword "^(alias|import|use|require)$")) @reference.import
