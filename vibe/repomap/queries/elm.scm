; Elm symbol extraction

(type_alias_declaration
  name: (upper_case_identifier) @name.definition.type) @definition.type

(type_declaration
  name: (upper_case_identifier) @name.definition.class) @definition.class

(value_declaration
  (function_declaration_left
    (lower_case_identifier) @name.definition.function)) @definition.function

(port_annotation
  name: (lower_case_identifier) @name.definition.function) @definition.function

(function_call_expr
  target: (value_expr
    (value_qid
      (lower_case_identifier) @name.reference.call))) @reference.call

(import_clause
  moduleName: (upper_case_qid) @name.reference.import) @reference.import

(exposing_list
  (exposed_value
    (lower_case_identifier) @name.reference.import)) @reference.import
