; OCaml symbol extraction

(value_definition
  (let_binding
    pattern: (value_name) @name.definition.function)) @definition.function

(value_definition
  (let_binding
    pattern: (value_name) @name.definition.property)) @definition.property

(type_definition
  (type_binding
    name: (type_constructor) @name.definition.type)) @definition.type

(module_definition
  (module_binding
    name: (module_name) @name.definition.class)) @definition.class

(module_type_definition
  name: (module_type_name) @name.definition.interface) @definition.interface

(class_definition
  (class_binding
    name: (class_name) @name.definition.class)) @definition.class

(application_expression
  function: (value_path
    (value_name) @name.reference.call)) @reference.call

(open_module
  (module_path
    (module_name) @name.reference.import)) @reference.import

(include_module
  (module_expression
    (module_path
      (module_name) @name.reference.import))) @reference.import
