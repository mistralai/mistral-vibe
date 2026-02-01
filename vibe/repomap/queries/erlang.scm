; Erlang symbol extraction

(function_clause
  name: (atom) @name.definition.function) @definition.function

(module_attribute
  name: (atom) @_keyword
  value: (atom) @name.definition.class
  (#eq? @_keyword "module")) @definition.class

(export_attribute
  (export
    (function_arity
      name: (atom) @name.definition.function))) @definition.function

(record_declaration
  name: (atom) @name.definition.class) @definition.class

(type_declaration
  name: (atom) @name.definition.type) @definition.type

(call
  function: (atom) @name.reference.call) @reference.call

(call
  function: (remote_call
    module: (atom)
    function: (atom) @name.reference.call)) @reference.call

(import_attribute
  (import
    module: (atom) @name.reference.import)) @reference.import

(include_lib_attribute
  (string) @name.reference.import) @reference.import
