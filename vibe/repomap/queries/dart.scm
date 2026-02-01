; Dart symbol extraction

(class_definition
  name: (identifier) @name.definition.class) @definition.class

(mixin_declaration
  name: (identifier) @name.definition.class) @definition.class

(enum_declaration
  name: (identifier) @name.definition.class) @definition.class

(extension_declaration
  name: (identifier) @name.definition.class) @definition.class

(function_signature
  name: (identifier) @name.definition.function) @definition.function

(method_signature
  name: (identifier) @name.definition.function) @definition.function

(getter_signature
  name: (identifier) @name.definition.property) @definition.property

(setter_signature
  name: (identifier) @name.definition.property) @definition.property

(constructor_signature
  name: (identifier) @name.definition.function) @definition.function

(initialized_variable_definition
  name: (identifier) @name.definition.property) @definition.property

(import_or_export
  (configurable_uri
    (uri) @name.reference.import)) @reference.import

(superclass
  (type_identifier) @name.reference.inherit) @reference.inherit

(interfaces
  (type_identifier) @name.reference.inherit) @reference.inherit
