; SQL symbol extraction

(create_table_statement
  name: (identifier) @name.definition.class) @definition.class

(create_table_statement
  name: (object_reference
    name: (identifier) @name.definition.class)) @definition.class

(create_view_statement
  name: (identifier) @name.definition.class) @definition.class

(create_function_statement
  name: (identifier) @name.definition.function) @definition.function

(create_procedure_statement
  name: (identifier) @name.definition.function) @definition.function

(create_index_statement
  name: (identifier) @name.definition.property) @definition.property

(create_trigger_statement
  name: (identifier) @name.definition.function) @definition.function

(function_call
  name: (identifier) @name.reference.call) @reference.call

(table_reference
  name: (identifier) @name.reference.call) @reference.call

(column_definition
  name: (identifier) @name.definition.property) @definition.property
