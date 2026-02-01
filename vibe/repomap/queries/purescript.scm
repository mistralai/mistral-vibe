; PureScript symbol extraction

(value_declaration
  name: (identifier) @name.definition.function) @definition.function

(signature
  name: (identifier) @name.definition.function) @definition.function

(data_declaration
  name: (type_constructor) @name.definition.class) @definition.class

(newtype_declaration
  name: (type_constructor) @name.definition.class) @definition.class

(type_synonym_declaration
  name: (type_constructor) @name.definition.type) @definition.type

(class_declaration
  name: (class_name) @name.definition.interface) @definition.interface

(foreign_import_declaration
  name: (identifier) @name.definition.function) @definition.function

(import_declaration
  module: (module_name) @name.reference.import) @reference.import

(instance_declaration
  (class_name) @name.reference.inherit) @reference.inherit

(application
  (expression
    (qualified_identifier
      (identifier) @name.reference.call))) @reference.call
